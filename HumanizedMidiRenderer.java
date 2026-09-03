import org.jfugue.pattern.Pattern;
import org.jfugue.player.Player;

import javax.sound.midi.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
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
 *
 * 11. Arpeggio source-boundary markers.
 * Explicit ARPEVENT markers preserve scheduled chord boundaries while
 * timing humanization is applied.
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

    private static final String ARPEGGIO_BOUNDARY_MARKER = "ARPEVENT";

    private static boolean isArpeggioBoundaryMarker(MidiEvent event) {
        if (!(event.getMessage() instanceof MetaMessage)) return false;
        MetaMessage meta = (MetaMessage) event.getMessage();
        return meta.getType() == 0x06
            && new String(meta.getData(), StandardCharsets.UTF_8)
                .startsWith(ARPEGGIO_BOUNDARY_MARKER);
    }

    private static List<Long> readArpeggioBoundaries(List<MidiEvent> events) {
        Set<Long> boundaries = new TreeSet<>();
        for (MidiEvent event : events) {
            if (isArpeggioBoundaryMarker(event)) {
                boundaries.add(event.getTick());
            }
        }
        return new ArrayList<>(boundaries);
    }

    private static long nextSourceBoundary(
        List<Long> boundaries,
        long sourceOnset
    ) {
        for (long boundary : boundaries) {
            if (boundary > sourceOnset) return boundary;
        }
        return Long.MAX_VALUE;
    }

    private static void enforceSourceBoundary(
        MidiEvent on,
        MidiEvent off,
        long boundary,
        long minNoteDurationTicks
    ) {
        if (boundary == Long.MAX_VALUE) return;

        long latestOnset = Math.max(0, boundary - minNoteDurationTicks);
        if (on.getTick() >= boundary) on.setTick(latestOnset);
        if (off.getTick() > boundary) off.setTick(boundary);

        long minimumOff = on.getTick() + minNoteDurationTicks;
        if (off.getTick() < minimumOff) {
            if (minimumOff > boundary) {
                on.setTick(latestOnset);
                minimumOff = on.getTick() + minNoteDurationTicks;
            }
            off.setTick(Math.min(boundary, minimumOff));
        }
    }

    private static int compareEvents(MidiEvent e1, MidiEvent e2) {
        int cmp = Long.compare(e1.getTick(), e2.getTick());
        if (cmp != 0) return cmp;
        if (isNoteOff(e1) && isNoteOn(e2)) return -1;
        if (isNoteOn(e1) && isNoteOff(e2)) return 1;
        return 0;
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
            
            events.sort(HumanizedMidiRenderer::compareEvents);
            List<Long> sourceBoundaries = readArpeggioBoundaries(events);

            // ── 2. Assign one timing offset per chord group ───────────────
            Map<Integer, Queue<Long>> noteOnOffsets = new HashMap<>();
            Map<Integer, Long>        lastOnTick    = new HashMap<>();
            Map<Integer, Long>        lastOnDelta   = new HashMap<>();
            Map<MidiEvent, Long> sourceBoundaryByOn = new IdentityHashMap<>();

            for (MidiEvent event : events) {
                if (!isNoteOn(event)) continue;
                
                ShortMessage sm = (ShortMessage) event.getMessage();
                int ch   = sm.getChannel();
                long tick = event.getTick();
                int key  = (ch << 7) | sm.getData1();
                sourceBoundaryByOn.put(
                    event, nextSourceBoundary(sourceBoundaries, tick)
                );

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
            // Pair overlapping notes in source order. A map of one active
            // note is insufficient when a pitch is retriggered before its
            // previous Note-Off.
            Map<Integer, Queue<MidiEvent>> activeNoteOns = new HashMap<>();
            Map<Integer, Queue<Long>> activeNoteOnDeltas = new HashMap<>();
            Map<Integer, List<MidiEvent[]>> notePairs = new HashMap<>();

            for (MidiEvent event : events) {
                if (!isNoteOn(event) && !isNoteOff(event)) continue;

                ShortMessage sm  = (ShortMessage) event.getMessage();
                int cmd = sm.getCommand();
                int ch  = sm.getChannel();
                int key = (ch << 7) | sm.getData1();

                long delta;
                if (isNoteOn(event)) {
                    Queue<Long> q = noteOnOffsets.get(key);
                    delta = (q != null && !q.isEmpty()) ? q.poll() : 0L;
                    activeNoteOnDeltas.computeIfAbsent(
                        key, k -> new LinkedList<>()
                    ).add(delta);
                    activeNoteOns.computeIfAbsent(
                        key, k -> new LinkedList<>()
                    ).add(event);
                } else {
                    Queue<Long> deltas = activeNoteOnDeltas.get(key);
                    delta = (
                        deltas != null && !deltas.isEmpty()
                    ) ? deltas.poll() : 0L;
                    Queue<MidiEvent> ons = activeNoteOns.get(key);
                    if (ons != null && !ons.isEmpty()) {
                        MidiEvent on = ons.poll();
                        notePairs.computeIfAbsent(
                            key, k -> new LinkedList<>()
                        ).add(new MidiEvent[] {on, event});
                    }
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
            for (List<MidiEvent[]> pairs : notePairs.values()) {
                for (MidiEvent[] pair : pairs) {
                    enforceSourceBoundary(
                        pair[0],
                        pair[1],
                        sourceBoundaryByOn.getOrDefault(
                            pair[0], Long.MAX_VALUE
                        ),
                        minNoteDurationTicks
                    );
                }
            }
            for (List<MidiEvent[]> pairs : notePairs.values()) {
                pairs.sort(Comparator.comparingLong(pair -> pair[0].getTick()));
                for (int i = 0; i < pairs.size(); i++) {
                    MidiEvent on = pairs.get(i)[0];
                    MidiEvent off = pairs.get(i)[1];
                    if (i > 0) {
                        MidiEvent previousOn = pairs.get(i - 1)[0];
                        MidiEvent previousOff = pairs.get(i - 1)[1];
                        long requiredOff = on.getTick() - retriggerGapTicks;
                        long previousBoundary = sourceBoundaryByOn.getOrDefault(
                            previousOn, Long.MAX_VALUE
                        );
                        if (previousBoundary != Long.MAX_VALUE) {
                            requiredOff = Math.min(
                                requiredOff, previousBoundary
                            );
                        }
                        if (previousOff.getTick() > requiredOff) {
                            long newOff = Math.max(
                                previousOn.getTick() + minNoteDurationTicks,
                                requiredOff
                            );
                            if (previousBoundary != Long.MAX_VALUE) {
                                newOff = Math.min(newOff, previousBoundary);
                            }
                            previousOff.setTick(newOff);
                            if (newOff > requiredOff) {
                                long shift = newOff + retriggerGapTicks
                                    - on.getTick();
                                on.setTick(on.getTick() + shift);
                                off.setTick(off.getTick() + shift);
                            }
                        }
                    }
                    long minOff = on.getTick() + minNoteDurationTicks;
                    if (off.getTick() < minOff) {
                        long boundary = sourceBoundaryByOn.getOrDefault(
                            on, Long.MAX_VALUE
                        );
                        off.setTick(
                            boundary == Long.MAX_VALUE
                                ? minOff
                                : Math.min(minOff, boundary)
                        );
                    }
                }
            }
            for (List<MidiEvent[]> pairs : notePairs.values()) {
                for (MidiEvent[] pair : pairs) {
                    enforceSourceBoundary(
                        pair[0],
                        pair[1],
                        sourceBoundaryByOn.getOrDefault(
                            pair[0], Long.MAX_VALUE
                        ),
                        minNoteDurationTicks
                    );
                }
            }

            // ── 7. Re-sort by tick and rebuild track ──────────────────────
            events.sort(HumanizedMidiRenderer::compareEvents);
            for (int i = track.size() - 1; i >= 0; i--) track.remove(track.get(i));
            for (MidiEvent event : events) track.add(event);
        }
    }
}