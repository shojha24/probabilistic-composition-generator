import org.jfugue.pattern.Pattern;
import org.jfugue.player.Player;

import javax.sound.midi.*;
import java.io.*;
import java.util.*;

/**
 * HumanizedMidiRenderer.java
 * ===========================
 * Reads JFugue Staccato strings, renders each song to a MIDI Sequence,
 * applies "Sim2Real" humanization, and writes .mid files.
 *
 * Humanization fixes
 * ──────────────────────────────────
 * 1. Chord-group timing.
 * Note-Ons within chordGroupingWindow ticks of each other on the same
 * channel share one timing offset, preventing chord-tone scatter.
 *
 * 2. Queue-based offset tracking (FIX A).
 * Each (channel, pitch) key maps to a Queue<Long> so that repeated
 * pitches are paired chronologically with their own Note-Offs.
 *
 * 3. Exact duration preservation (FIX A continued).
 * Note-Off inherits its paired Note-On's delta.
 *
 * 4. MIN_NOTE_DURATION_TICKS floor.
 * Guards against tick-0 clamping collapsing very short notes.
 *
 * 5. Dynamic chord-grouping window (FIX C).
 * Scales correctly at any sequence resolution (PPQ / 48).
 *
 * 6. Sort after duration enforcement (FIX B).
 * Restores MIDI-spec requirement of ascending ticks.
 *
 * 7. Velocity humanization with per-channel sigma.
 *
 * 8. Per-channel timing bias.
 *
 * 9. Tempo-aware timing sigma (FIX D).
 * Jitter is derived at runtime from the MIDI tempo meta-event, keeping
 * the perceptual variation constant regardless of BPM.
 *
 * 10. Retrigger gap and Overlap resolution (FIX E).
 * Detects identical consecutive notes and forces a short retrigger gap.
 * Safely resolves instances where Gaussian jitter causes a previous 
 * Note-Off to cross over a subsequent Note-On for the same pitch.
 */
public class HumanizedMidiRenderer {

    // ── Configuration ────────────────────────────────────────────────────────

    private static String INPUT_FILE = "gen/generated_scores.txt";
    private static String OUTPUT_DIR = "gen/midi_output";        

    private static final double TARGET_JITTER_MS = 15.0;
    private static final double VELOCITY_JITTER_SIGMA = 8.0;

    private static final Map<Integer, Double> CHANNEL_BIAS_MS = new HashMap<>();
    static {
        CHANNEL_BIAS_MS.put(0,  0.0);    // Pad        -- on the beat
        CHANNEL_BIAS_MS.put(1,  8.0);    // Bass       -- slightly behind (pocket feel)
        CHANNEL_BIAS_MS.put(2, -4.0);    // Extensions -- slightly ahead (airy)
        CHANNEL_BIAS_MS.put(3, -6.0);    // Arp        -- rushing slightly
        CHANNEL_BIAS_MS.put(9,  2.0);    // Drums      -- fractionally late (groove)
    }

    // ── Entry point ──────────────────────────────────────────────────────────

    public static void main(String[] args) {
        // Add this check right at the start of main()
        if (args.length >= 2) {
            INPUT_FILE = args[0];
            OUTPUT_DIR = args[1];
        }

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
            Pattern  pattern  = new Pattern(jfugueString);
            Sequence sequence = new Player().getSequence(pattern);

            humanizeSequence(sequence, new Random());

            File outFile = new File(OUTPUT_DIR, name + ".mid");
            MidiSystem.write(sequence, 1, outFile);
            System.out.println("  Rendered: " + name);

        } catch (Exception e) {
            System.err.println("  ERROR rendering " + name + ": " + e.getMessage());
            e.printStackTrace();
        }
    }

    // ── Tempo helper ──────────────────────────────────────────────────────────

    private static int readTempoMicros(Sequence sequence) {
        for (Track track : sequence.getTracks()) {
            for (int i = 0; i < track.size(); i++) {
                MidiMessage msg = track.get(i).getMessage();
                if (msg instanceof MetaMessage) {
                    MetaMessage meta = (MetaMessage) msg;
                    if (meta.getType() == 0x51) {
                        byte[] data = meta.getData();
                        return ((data[0] & 0xFF) << 16)
                             | ((data[1] & 0xFF) << 8)
                             |  (data[2] & 0xFF);
                    }
                }
            }
        }
        return 500_000; // default: 120 BPM
    }

    // ── MIDI Event Helpers ────────────────────────────────────────────────────

    private static boolean isNoteOn(MidiEvent e) {
        if (!(e.getMessage() instanceof ShortMessage)) return false;
        ShortMessage sm = (ShortMessage) e.getMessage();
        return sm.getCommand() == ShortMessage.NOTE_ON && sm.getData2() > 0;
    }

    private static boolean isNoteOff(MidiEvent e) {
        if (!(e.getMessage() instanceof ShortMessage)) return false;
        ShortMessage sm = (ShortMessage) e.getMessage();
        return sm.getCommand() == ShortMessage.NOTE_OFF 
            || (sm.getCommand() == ShortMessage.NOTE_ON && sm.getData2() == 0);
    }

    // ── Humanization ──────────────────────────────────────────────────────────

    private static void humanizeSequence(Sequence sequence, Random rng) {

        int    tempoMicros = readTempoMicros(sequence);
        int    ppq         = sequence.getResolution();
        double msPerTick   = tempoMicros / (ppq * 1000.0);
        double tickSigma   = TARGET_JITTER_MS / msPerTick;

        final long chordGroupingWindow  = Math.max(1, ppq / 48);
        final long minNoteDurationTicks = Math.max(1, ppq / 8);

        for (Track track : sequence.getTracks()) {

            // ── 1. Collect and logically sort ─────────────────────────────
            List<MidiEvent> events = new ArrayList<>();
            for (int i = 0; i < track.size(); i++) events.add(track.get(i));
            
            events.sort((e1, e2) -> {
                int cmp = Long.compare(e1.getTick(), e2.getTick());
                if (cmp != 0) return cmp;
                // Same tick: guarantee Note-Offs come before Note-Ons
                if (isNoteOff(e1) && isNoteOn(e2)) return -1;
                if (isNoteOn(e1) && isNoteOff(e2)) return 1;
                return 0;
            });

            // ── 2. Assign one timing offset per chord group ───────────────
            Map<Integer, Queue<Long>> noteOnOffsets = new HashMap<>();
            Map<Integer, Long>        lastOnTick    = new HashMap<>();
            Map<Integer, Long>        lastOnDelta   = new HashMap<>();

            for (MidiEvent event : events) {
                if (!isNoteOn(event)) continue;
                
                ShortMessage sm = (ShortMessage) event.getMessage();
                int ch   = sm.getChannel();
                long tick = event.getTick();
                int key  = (ch << 7) | sm.getData1();

                Long prevTick  = lastOnTick.get(ch);
                Long prevDelta = lastOnDelta.get(ch);

                long delta;
                if (prevTick != null && (tick - prevTick) <= chordGroupingWindow && prevDelta != null) {
                    delta = prevDelta;
                } else {
                    double biasMs    = CHANNEL_BIAS_MS.getOrDefault(ch, 0.0);
                    double biasTicks = biasMs / msPerTick;
                    delta = Math.round(rng.nextGaussian() * tickSigma + biasTicks);
                    lastOnTick.put(ch, tick);
                    lastOnDelta.put(ch, delta);
                }

                noteOnOffsets.computeIfAbsent(key, k -> new LinkedList<>()).add(delta);
            }

            // ── 3 & 4. Apply offsets and velocity jitter ──────────────────
            Map<Integer, Long> activeNoteOnDelta = new HashMap<>();

            for (MidiEvent event : events) {
                if (!isNoteOn(event) && !isNoteOff(event)) continue;

                ShortMessage sm  = (ShortMessage) event.getMessage();
                int cmd = sm.getCommand();
                int ch  = sm.getChannel();
                int key = (ch << 7) | sm.getData1();

                long delta;
                if (isNoteOn(event)) {
                    Queue<Long> q = noteOnOffsets.get(key);
                    delta = (q != null && !q.isEmpty()) ? q.peek() : 0L;
                    activeNoteOnDelta.put(key, delta);
                } else {
                    Long stashed = activeNoteOnDelta.remove(key);
                    delta = (stashed != null) ? stashed : 0L;
                    Queue<Long> q = noteOnOffsets.get(key);
                    if (q != null) q.poll();
                }

                event.setTick(Math.max(0, event.getTick() + delta));

                if (isNoteOn(event)) {
                    double sigma = (ch == 9) ? VELOCITY_JITTER_SIGMA * 0.5 : VELOCITY_JITTER_SIGMA;
                    int newVel = (int) Math.round(sm.getData2() + rng.nextGaussian() * sigma);
                    newVel = Math.max(1, Math.min(127, newVel));
                    try {
                        sm.setMessage(cmd, ch, sm.getData1(), newVel);
                    } catch (InvalidMidiDataException ignored) {}
                }
            }

            // ── 5. Retrigger gap & Overlap resolution (FIX E) ─────────────
            final long retriggerGapTicks = Math.max(1, ppq / 48); // ~10 ticks

            Map<Integer, MidiEvent> lastNoteOff = new HashMap<>();
            Map<MidiEvent, MidiEvent> offToOnMap = new HashMap<>();
            Map<Integer, MidiEvent> activeOn = new HashMap<>();

            for (MidiEvent event : events) {
                if (isNoteOn(event)) {
                    ShortMessage sm = (ShortMessage) event.getMessage();
                    int key = (sm.getChannel() << 7) | sm.getData1();
                    
                    activeOn.put(key, event);

                    MidiEvent prevOff = lastNoteOff.get(key);
                    if (prevOff != null) {
                        long requiredOffTick = event.getTick() - retriggerGapTicks;
                        
                        if (prevOff.getTick() > requiredOffTick) {
                            MidiEvent itsOwnOn = offToOnMap.get(prevOff);
                            long absoluteMinOff = (itsOwnOn != null) 
                                    ? itsOwnOn.getTick() + minNoteDurationTicks 
                                    : 0;

                            long newOffTick = Math.max(absoluteMinOff, requiredOffTick);
                            prevOff.setTick(newOffTick);

                            if (newOffTick > requiredOffTick) {
                                // Pushing Note-Off back hit the duration floor. 
                                // Push the next Note-On forward to guarantee the gap.
                                event.setTick(newOffTick + retriggerGapTicks);
                            }
                        }
                    }
                } else if (isNoteOff(event)) {
                    ShortMessage sm = (ShortMessage) event.getMessage();
                    int key = (sm.getChannel() << 7) | sm.getData1();
                    
                    lastNoteOff.put(key, event);
                    MidiEvent on = activeOn.remove(key);
                    if (on != null) {
                        offToOnMap.put(event, on);
                    }
                }
            }

            // ── 6. Enforce minimum note duration ──────────────────────────
            for (Map.Entry<MidiEvent, MidiEvent> entry : offToOnMap.entrySet()) {
                MidiEvent off = entry.getKey();
                MidiEvent on  = entry.getValue();
                long minOff = on.getTick() + minNoteDurationTicks;
                if (off.getTick() < minOff) {
                    off.setTick(minOff);
                }
            }

            // ── 7. Re-sort by tick and rebuild track ──────────────────────
            events.sort(Comparator.comparingLong(MidiEvent::getTick));
            for (int i = track.size() - 1; i >= 0; i--) track.remove(track.get(i));
            for (MidiEvent event : events) track.add(event);
        }
    }
}