import org.jfugue.pattern.Pattern;
import org.jfugue.player.Player;
import org.jfugue.midi.MidiFileManager;

import javax.sound.midi.*;
import java.io.*;
import java.util.*;

/**
 * HumanizedMidiRenderer.java  —  rewrite
 * ========================================
 * Reads the JFugue Staccato strings produced by MultitrackComposer,
 * renders each song to a MIDI Sequence, applies "Sim2Real" humanization,
 * and writes .mid files.
 *
 * ROOT CAUSES FIXED vs. original
 * ────────────────────────────────
 * 1. Track sort after jitter.
 *    The original jittered tick values but never re-sorted the track.
 *    MIDI events MUST be ordered by ascending tick; out-of-order Note-Off
 *    before Note-On produces instant release (click / silence).
 *    FIX: collect all events, sort by tick, rebuild the track.
 *
 * 2. Note-Off clamping.
 *    After sorting, a Note-Off could still land on the same tick or before
 *    its Note-On if the jitter on the Off was strongly negative.
 *    FIX: track the last Note-On tick per (channel, pitch) and ensure the
 *    corresponding Note-Off is at least MIN_NOTE_DURATION_TICKS later.
 *
 * 3. Velocity humanization.
 *    Original only jittered timing.  Real performances vary velocity.
 *    FIX: add Gaussian velocity jitter (σ = VELOCITY_JITTER), clamped [1,127].
 *    Percussion (channel 9) gets its own narrower range.
 *
 * 4. Micro-timing per-voice bias.
 *    In a real ensemble the bassist lags slightly, the arpeggiator rushes.
 *    FIX: per-channel tick bias added before Gaussian jitter.
 */
public class HumanizedMidiRenderer {

    // ── Configuration ────────────────────────────────────────────────────────

    private static final String INPUT_FILE  = "gen/generated_scores.txt";
    private static final String OUTPUT_DIR  = "gen/midi_output";

    /**
     * Gaussian σ for timing jitter in ticks.
     * At 480 PPQ / 120 BPM one quarter note = 480 ticks ≈ 500 ms.
     * σ = 15 ticks ≈ 15 ms — subtle but audible humanization.
     */
    private static final double TIMING_JITTER_SIGMA   = 15.0;

    /**
     * Gaussian σ for velocity jitter.  Full range 0–127, σ = 8 keeps most
     * notes within ±16 of their programmed velocity.
     */
    private static final double VELOCITY_JITTER_SIGMA = 8.0;

    /**
     * Minimum gap (ticks) between a Note-On and its matching Note-Off.
     * Prevents zero-duration notes after tick clamping.
     */
    private static final long MIN_NOTE_DURATION_TICKS = 10;

    /**
     * Per-channel timing bias in ticks (positive = late, negative = early).
     * Channel indices follow GM (0-based): 0=pad, 1=bass, 2=ext, 3=arp, 9=drums.
     */
    private static final Map<Integer, Double> CHANNEL_BIAS = new HashMap<>();
    static {
        CHANNEL_BIAS.put(0,  0.0);   // Pad  — on the beat
        CHANNEL_BIAS.put(1,  8.0);   // Bass — slightly behind (pocket feel)
        CHANNEL_BIAS.put(2, -4.0);   // Extensions — slightly ahead (airy)
        CHANNEL_BIAS.put(3, -6.0);   // Arp — rushing slightly
        CHANNEL_BIAS.put(9,  2.0);   // Drums — fractionally late (groove)
    }

    // ── Entry point ──────────────────────────────────────────────────────────

    public static void main(String[] args) {
        new File(OUTPUT_DIR).mkdirs();

        try (BufferedReader br = new BufferedReader(new FileReader(INPUT_FILE))) {
            String line;
            StringBuilder songBuffer = new StringBuilder();
            String currentSongName   = "";
            boolean recording        = false;

            System.out.println("--- Starting Batch Renderer ---");

            while ((line = br.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty()) continue;

                if (line.startsWith("START_SONG_")) {
                    currentSongName = line;
                    songBuffer.setLength(0);
                    recording = true;

                } else if (line.startsWith("END_SONG")) {
                    if (recording && songBuffer.length() > 0) {
                        processSong(songBuffer.toString().trim(), currentSongName);
                        recording = false;
                    }

                } else if (recording) {
                    if (songBuffer.length() > 0) songBuffer.append(' ');
                    songBuffer.append(line);
                }
            }
            System.out.println("--- Rendering Complete ---");

        } catch (IOException e) {
            System.err.println("I/O error reading " + INPUT_FILE + ": " + e.getMessage());
            e.printStackTrace();
        }
    }

    // ── Song pipeline ─────────────────────────────────────────────────────────

    private static void processSong(String jfugueString, String name) {
        try {
            // Step 1: JFugue → raw MIDI Sequence
            Pattern  pattern  = new Pattern(jfugueString);
            Sequence sequence = new Player().getSequence(pattern);

            // Step 2: Humanize
            humanizeSequence(sequence, new Random());

            // Step 3: Write
            File outFile = new File(OUTPUT_DIR, name + ".mid");
            MidiSystem.write(sequence, 1, outFile);
            System.out.println("  Rendered: " + name);

        } catch (Exception e) {
            System.err.println("  ERROR rendering " + name + ": " + e.getMessage());
            e.printStackTrace();
        }
    }

    // ── Humanization ──────────────────────────────────────────────────────────

    /**
     * Apply timing + velocity humanization to every track in the sequence.
     *
     * Algorithm per track
     * ───────────────────
     * 1. Collect all MidiEvents into a mutable list.
     * 2. For each Note-On / Note-Off:
     *    a. Determine the MIDI channel of this event.
     *    b. Apply per-channel bias + Gaussian timing jitter.
     *    c. Clamp tick ≥ 0.
     *    d. Apply Gaussian velocity jitter (Note-On only), clamped [1, 127].
     * 3. Sort the event list by tick (ascending).
     * 4. Enforce MIN_NOTE_DURATION_TICKS: scan for Note-Off events and ensure
     *    they occur at least MIN_NOTE_DURATION_TICKS after their Note-On.
     * 5. Rebuild the Track from the sorted, clamped list.
     */
    private static void humanizeSequence(Sequence sequence, Random rng) {
        for (Track track : sequence.getTracks()) {
            List<MidiEvent> events = new ArrayList<>();

            // Collect
            for (int i = 0; i < track.size(); i++) {
                events.add(track.get(i));
            }

            // Jitter timing + velocity
            for (MidiEvent event : events) {
                MidiMessage msg = event.getMessage();
                if (!(msg instanceof ShortMessage)) continue;

                ShortMessage sm  = (ShortMessage) msg;
                int cmd          = sm.getCommand();
                boolean isNoteOn = (cmd == ShortMessage.NOTE_ON && sm.getData2() > 0);
                boolean isNoteOff = (cmd == ShortMessage.NOTE_OFF)
                                 || (cmd == ShortMessage.NOTE_ON && sm.getData2() == 0);

                if (!isNoteOn && !isNoteOff) continue;

                int channel = sm.getChannel();
                double bias = CHANNEL_BIAS.getOrDefault(channel, 0.0);

                // Timing
                double jitter  = rng.nextGaussian() * TIMING_JITTER_SIGMA + bias;
                long newTick   = Math.max(0, event.getTick() + Math.round(jitter));
                event.setTick(newTick);

                // Velocity (Note-On only)
                if (isNoteOn) {
                    int origVel  = sm.getData2();
                    double sigma = (channel == 9)
                                   ? VELOCITY_JITTER_SIGMA * 0.5   // drums: tighter
                                   : VELOCITY_JITTER_SIGMA;
                    int newVel   = (int) Math.round(origVel + rng.nextGaussian() * sigma);
                    newVel       = Math.max(1, Math.min(127, newVel));
                    try {
                        sm.setMessage(sm.getCommand(), sm.getChannel(), sm.getData1(), newVel);
                    } catch (InvalidMidiDataException ignored) { /* keep original */ }
                }
            }

            // Sort by tick (ascending); stable sort preserves relative order
            // of events on the same tick (e.g. Note-Off before Note-On).
            events.sort(Comparator.comparingLong(MidiEvent::getTick));

            // Enforce minimum note duration
            // Map: (channel << 7 | pitch) → tick of last Note-On
            Map<Integer, Long> noteOnTicks = new HashMap<>();
            for (MidiEvent event : events) {
                MidiMessage msg = event.getMessage();
                if (!(msg instanceof ShortMessage)) continue;
                ShortMessage sm = (ShortMessage) msg;
                int cmd   = sm.getCommand();
                int pitch = sm.getData1();
                int ch    = sm.getChannel();
                int key   = (ch << 7) | pitch;

                boolean isNoteOn  = (cmd == ShortMessage.NOTE_ON && sm.getData2() > 0);
                boolean isNoteOff = (cmd == ShortMessage.NOTE_OFF)
                                 || (cmd == ShortMessage.NOTE_ON && sm.getData2() == 0);

                if (isNoteOn) {
                    noteOnTicks.put(key, event.getTick());
                } else if (isNoteOff) {
                    Long onTick = noteOnTicks.get(key);
                    if (onTick != null) {
                        long minOff = onTick + MIN_NOTE_DURATION_TICKS;
                        if (event.getTick() < minOff) {
                            event.setTick(minOff);
                        }
                        noteOnTicks.remove(key);
                    }
                }
            }

            // Rebuild the track
            // Track.remove() is O(n²) for large tracks; clear by removing all
            // then re-add. The Track API doesn't have a clear() method, so we
            // remove backwards (safer — avoids index shifting issues).
            for (int i = track.size() - 1; i >= 0; i--) {
                track.remove(track.get(i));
            }
            for (MidiEvent event : events) {
                track.add(event);
            }
        }
    }
}