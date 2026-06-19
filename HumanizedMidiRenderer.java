import org.jfugue.pattern.Pattern;
import org.jfugue.player.Player;
import org.jfugue.midi.MidiFileManager;

import javax.sound.midi.*;
import java.io.*;
import java.util.*;

/**
 * HumanizedMidiRenderer.java
 * ===========================
 * Reads JFugue Staccato strings, renders each song to a MIDI Sequence,
 * applies "Sim2Real" humanization, and writes .mid files.
 *
 * FIXES vs. previous version
 * ──────────────────────────
 * 1. Chord-group timing.
 *    Note-Ons within CHORD_GROUPING_WINDOW_TICKS of each other on the same
 *    channel are treated as one chord and receive a single shared timing
 *    offset.  Previously every note drew its own offset independently,
 *    scattering simultaneous chord tones across time.
 *
 * 2. Note-Off tracks its paired Note-On offset.
 *    Note-Offs now receive the exact same tick delta that was applied to
 *    their matching Note-On, so note duration is preserved.  Previously
 *    Note-Offs were jittered independently, causing random early cutoffs.
 *
 * 3. MIN_NOTE_DURATION_TICKS raised to 120.
 *    The old value of 10 ticks (~12 ms at 480 PPQ / 120 BPM) was too small
 *    to catch Note-Offs that snuck in early after negative jitter.
 *    120 ticks ≈ one 16th note — a firm floor that still sounds natural.
 *
 * Earlier fixes (retained)
 * ────────────────────────
 * 4. Track sort after jitter — MIDI events must be ascending by tick.
 * 5. Velocity humanization with per-channel sigma.
 * 6. Per-channel timing bias (pocket feel, rushing arp, etc.).
 *
 * NEW fixes in this version
 * ─────────────────────────
 * FIX A. Queue-based offset tracking (replaces flat Map<Integer,Long>).
 *    The old noteOnOffset map stored one Long per (channel,pitch) key,
 *    meaning the second occurrence of the same pitch on the same channel
 *    silently overwrote the first.  In Step 3, the first Note-On would
 *    then receive the delta computed for the second occurrence, tearing it
 *    away from its own chord group.  We now store a Queue<Long> per key
 *    and consume entries in chronological order: Note-On peek()s (reads
 *    its delta without consuming), and its matching Note-Off poll()s
 *    (consumes the entry).  This pairs every Note-On to exactly its own
 *    Note-Off regardless of repetition count.
 *
 * FIX B. Final sort pass after duration enforcement (Step 6.5).
 *    Step 6 can push Note-Off events forward in time, potentially past
 *    later Note-Ons that were not moved.  Writing an unsorted event list
 *    into a MIDI track violates the MIDI spec and can corrupt playback.
 *    A sort between Step 6 and Step 7 (Rebuild) guarantees ascending
 *    tick order before writing.
 *
 * FIX C. Dynamic CHORD_GROUPING_WINDOW_TICKS based on Sequence PPQ.
 *    The old hardcoded 10-tick window was calibrated to 480 PPQ.  At
 *    higher PPQ values JFugue can spread chord tones further apart,
 *    re-introducing the scatter bug.  The window is now computed as
 *    PPQ / 48, which always equals one 128th note regardless of PPQ:
 *      480 PPQ  → 10 ticks   (same as before at standard resolution)
 *      960 PPQ  → 20 ticks   (correct scaling at double resolution)
 *      192 PPQ  →  4 ticks   (correct scaling at low resolution)
 */
public class HumanizedMidiRenderer {

    // ── Configuration ────────────────────────────────────────────────────────

    private static final String INPUT_FILE  = "gen/generated_scores.txt";
    private static final String OUTPUT_DIR  = "gen/midi_output";

    /** Gaussian σ for timing jitter in ticks (≈ 15 ms at 480 PPQ / 120 BPM). */
    private static final double TIMING_JITTER_SIGMA = 15.0;

    /** Gaussian σ for velocity jitter.  σ = 8 keeps most notes within ±16. */
    private static final double VELOCITY_JITTER_SIGMA = 8.0;

    /**
     * Minimum gap (ticks) between a Note-On and its matching Note-Off.
     * 120 ticks ≈ one 32nd note at 480 PPQ / 120 BPM.
     */
    private static final long MIN_NOTE_DURATION_TICKS = 120;

    /**
     * REMOVED as a static constant — now computed dynamically per-sequence
     * in humanizeSequence() as (ppq / 48), which equals one 128th note at
     * any PPQ value.  See FIX C.
     *
     * private static final long CHORD_GROUPING_WINDOW_TICKS = 10;
     */

    /**
     * Per-channel timing bias in ticks (positive = late, negative = early).
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

    // ── Humanization ──────────────────────────────────────────────────────────

    /**
     * Apply timing + velocity humanization to every track in the sequence.
     *
     * Algorithm per track
     * ───────────────────
     * 1.   Collect all MidiEvents and sort by tick.
     * 2.   Walk Note-Ons in tick order.  Group simultaneous notes (within
     *      chordGroupingWindow ticks on the same channel) and assign one
     *      shared Gaussian timing offset to the whole group.
     *      [FIX A] Enqueue the delta into a Queue<Long> per (ch,pitch) key
     *      so that repeated pitches are tracked independently and in order.
     * 3.   Apply that offset to each Note-On (peek) and its paired Note-Off
     *      (poll) so duration is preserved exactly.
     * 4.   Apply Gaussian velocity jitter to Note-Ons only, clamped [1,127].
     * 5.   Re-sort by tick.
     * 6.   Enforce MIN_NOTE_DURATION_TICKS between every Note-On and its Off.
     * 6.5  [FIX B] Re-sort again — Step 6 can push Note-Offs past later
     *      Note-Ons, breaking MIDI chronological ordering.
     * 7.   Rebuild the Track.
     *
     * @param sequence  the Sequence to modify in place
     * @param rng       caller-supplied Random for reproducibility if desired
     */
    private static void humanizeSequence(Sequence sequence, Random rng) {

        // ── FIX C: derive chord-grouping window from actual PPQ ───────────
        //
        // PPQ (Pulses Per Quarter Note) is the sequence's tick resolution.
        // Dividing by 48 gives one 128th note, which is the tightest
        // rhythmic interval that can exist in standard notation and therefore
        // a safe upper bound for "these notes are meant to be simultaneous."
        //
        // At 480 PPQ  → window = 10 ticks  (same as the old hardcoded value)
        // At 960 PPQ  → window = 20 ticks  (scales correctly)
        // At 192 PPQ  → window =  4 ticks  (scales correctly)
        //
        // Math.max(..., 1) guards against a PPQ so low the division rounds to 0.
        final long chordGroupingWindow = Math.max(1, sequence.getResolution() / 48);

        for (Track track : sequence.getTracks()) {

            // ── 1. Collect and sort ───────────────────────────────────────
            List<MidiEvent> events = new ArrayList<>();
            for (int i = 0; i < track.size(); i++) events.add(track.get(i));
            events.sort(Comparator.comparingLong(MidiEvent::getTick));

            // ── 2. Assign one timing offset per chord group ───────────────
            //
            // FIX A — Queue-based offset storage.
            //
            // OLD CODE used:
            //   Map<Integer, Long> noteOnOffset = new HashMap<>();
            //   ...
            //   noteOnOffset.put(key, delta);   // OVERWRITES previous entry!
            //
            // If C4 appeared in measures 1 and 5, measure 5's delta would
            // overwrite measure 1's.  In Step 3, the measure-1 Note-On would
            // receive measure 5's delta, tearing it from its chord group.
            //
            // NEW CODE uses a queue per key.  Each Note-On *enqueues* its
            // delta.  In Step 3, a Note-On peek()s (reads without consuming)
            // and its matching Note-Off poll()s (consumes).  Every Note-On
            // is therefore paired with the delta that was computed for it,
            // never with a later recurrence of the same pitch.
            Map<Integer, Queue<Long>> noteOnOffsets = new HashMap<>();

            // Per-channel state for chord grouping (unchanged from original)
            Map<Integer, Long> lastOnTick  = new HashMap<>();
            Map<Integer, Long> lastOnDelta = new HashMap<>();

            for (MidiEvent event : events) {
                MidiMessage msg = event.getMessage();
                if (!(msg instanceof ShortMessage)) continue;
                ShortMessage sm = (ShortMessage) msg;
                if (sm.getCommand() != ShortMessage.NOTE_ON || sm.getData2() == 0) continue;

                int  ch   = sm.getChannel();
                long tick = event.getTick();
                int  key  = (ch << 7) | sm.getData1();

                Long prevTick  = lastOnTick.get(ch);
                Long prevDelta = lastOnDelta.get(ch);

                long delta;
                if (prevTick  != null
                        && (tick - prevTick) <= chordGroupingWindow   // FIX C: dynamic window
                        && prevDelta != null) {
                    // Same chord group — reuse the group's offset
                    delta = prevDelta;
                } else {
                    // New chord — draw a fresh Gaussian offset
                    double bias = CHANNEL_BIAS.getOrDefault(ch, 0.0);
                    delta = Math.round(rng.nextGaussian() * TIMING_JITTER_SIGMA + bias);
                    lastOnTick.put(ch, tick);
                    lastOnDelta.put(ch, delta);
                }

                // FIX A: enqueue rather than overwrite
                noteOnOffsets
                    .computeIfAbsent(key, k -> new LinkedList<>())
                    .add(delta);
            }

            // ── 3 & 4. Apply offsets and velocity jitter ──────────────────
            //
            // FIX A continued.
            //
            // We need two maps here that work in tandem:
            //
            //   pendingNoteOnDelta: (ch,pitch) → Queue<Long>
            //     The same queue we built in Step 2.  A Note-On peek()s the
            //     front of its queue to get its delta, but does NOT consume
            //     it yet — the Note-Off needs to read the same value.
            //
            //   activeNoteOnDelta: (ch,pitch) → Long
            //     Once a Note-On has peek()d and applied its delta, we stash
            //     that value here.  When the matching Note-Off arrives, it
            //     reads from activeNoteOnDelta and then poll()s the queue to
            //     discard the consumed entry.
            //
            // This two-map handshake is the key mechanism:
            //   Note-On  → peek queue  → stash in active map
            //   Note-Off → read active map → poll queue (consume)
            //
            // Without the active map, a Note-Off would have to peek the
            // queue itself, but by the time the Note-Off is processed the
            // queue may have already had its front entry consumed by a later
            // Note-On of the same pitch that arrived before this Note-Off
            // in tick order (an edge case for very short, rapid repeated
            // notes).

            // Re-use the queue map built in Step 2 as pendingNoteOnDelta.
            // activeNoteOnDelta holds the delta of the most recently started
            // (but not yet ended) note for each (ch,pitch) key.
            Map<Integer, Long> activeNoteOnDelta = new HashMap<>();

            for (MidiEvent event : events) {
                MidiMessage msg = event.getMessage();
                if (!(msg instanceof ShortMessage)) continue;
                ShortMessage sm = (ShortMessage) msg;
                int cmd = sm.getCommand();
                int ch  = sm.getChannel();
                int key = (ch << 7) | sm.getData1();

                boolean isNoteOn  = (cmd == ShortMessage.NOTE_ON  && sm.getData2() > 0);
                boolean isNoteOff = (cmd == ShortMessage.NOTE_OFF)
                                 || (cmd == ShortMessage.NOTE_ON  && sm.getData2() == 0);

                if (!isNoteOn && !isNoteOff) continue;

                long delta;

                if (isNoteOn) {
                    // Peek the front of this pitch's queue.
                    // peek() returns null if the queue is empty or absent,
                    // which should never happen for well-formed MIDI, but we
                    // default to 0 to avoid a NullPointerException.
                    Queue<Long> q = noteOnOffsets.get(key);
                    delta = (q != null && !q.isEmpty()) ? q.peek() : 0L;

                    // Stash for the matching Note-Off.
                    activeNoteOnDelta.put(key, delta);

                } else {
                    // Note-Off: retrieve the stashed delta and consume the
                    // queue entry so the next Note-On of this pitch gets its
                    // own fresh delta.
                    Long stashed = activeNoteOnDelta.remove(key);
                    delta = (stashed != null) ? stashed : 0L;

                    Queue<Long> q = noteOnOffsets.get(key);
                    if (q != null) q.poll();  // consume — this pitch's slot is done
                }

                // Shift the tick; clamp to zero to avoid negative timestamps.
                event.setTick(Math.max(0, event.getTick() + delta));

                // Velocity jitter on Note-Ons only.
                if (isNoteOn) {
                    double sigma = (ch == 9)
                                   ? VELOCITY_JITTER_SIGMA * 0.5
                                   : VELOCITY_JITTER_SIGMA;
                    int newVel = (int) Math.round(
                            sm.getData2() + rng.nextGaussian() * sigma);
                    newVel = Math.max(1, Math.min(127, newVel));
                    try {
                        sm.setMessage(cmd, ch, sm.getData1(), newVel);
                    } catch (InvalidMidiDataException ignored) {}
                }
            }

            // ── 5. Re-sort by tick ────────────────────────────────────────
            events.sort(Comparator.comparingLong(MidiEvent::getTick));

            // ── 6. Enforce minimum note duration ──────────────────────────
            Map<Integer, Long> noteOnTicks = new HashMap<>();
            for (MidiEvent event : events) {
                MidiMessage msg = event.getMessage();
                if (!(msg instanceof ShortMessage)) continue;
                ShortMessage sm = (ShortMessage) msg;
                int cmd = sm.getCommand();
                int key = (sm.getChannel() << 7) | sm.getData1();

                boolean isNoteOn  = (cmd == ShortMessage.NOTE_ON  && sm.getData2() > 0);
                boolean isNoteOff = (cmd == ShortMessage.NOTE_OFF)
                                 || (cmd == ShortMessage.NOTE_ON  && sm.getData2() == 0);

                if (isNoteOn) {
                    noteOnTicks.put(key, event.getTick());
                } else if (isNoteOff) {
                    Long onTick = noteOnTicks.get(key);
                    if (onTick != null) {
                        long minOff = onTick + MIN_NOTE_DURATION_TICKS;
                        if (event.getTick() < minOff) event.setTick(minOff);
                        noteOnTicks.remove(key);
                    }
                }
            }

            // ── 6.5 Re-sort after duration enforcement ────────────────────
            //
            // FIX B.
            //
            // Step 6 only ever moves Note-Off events forward in time, but
            // that forward movement can push a Note-Off past a later Note-On
            // that was not moved.  Example:
            //
            //   Before Step 6:
            //     tick 480  NOTE_ON  C4   ← measure 1 note, started late by jitter
            //     tick 490  NOTE_ON  D4   ← measure 1 note
            //     tick 500  NOTE_OFF C4   ← would end at tick 500
            //     tick 960  NOTE_ON  C4   ← measure 2 note
            //
            //   Step 6 computes: minOff for C4 = 480 + 120 = 600.
            //   tick 500 < 600, so NOTE_OFF C4 is pushed to tick 600.
            //
            //   After Step 6 (still in the list order from Step 5):
            //     tick 480  NOTE_ON  C4
            //     tick 490  NOTE_ON  D4
            //     tick 600  NOTE_OFF C4   ← moved forward ✓, but list is still sorted here
            //     tick 960  NOTE_ON  C4
            //
            // In this example we got lucky — the pushed Note-Off is still
            // before the next Note-On.  But consider a case where the gap is
            // small and jitter is large:
            //
            //   tick 480  NOTE_ON  C4    (note plays for a very short time)
            //   tick 481  NOTE_OFF C4    (moved to tick 480+120 = 600 by Step 6)
            //   tick 550  NOTE_ON  E4    (not moved — was already at 550)
            //   tick 560  NOTE_OFF E4
            //
            // After Step 6:
            //   tick 480  NOTE_ON  C4
            //   tick 600  NOTE_OFF C4   ← now AFTER the 550 NOTE_ON E4!
            //   tick 550  NOTE_ON  E4   ← still at 550 in the list
            //   tick 560  NOTE_OFF E4
            //
            // The list is now out of tick order.  Writing it to a MIDI Track
            // in this order can cause playback engines to misinterpret events.
            // A sort here restores the invariant.
            events.sort(Comparator.comparingLong(MidiEvent::getTick));

            // ── 7. Rebuild the track ──────────────────────────────────────
            for (int i = track.size() - 1; i >= 0; i--) track.remove(track.get(i));
            for (MidiEvent event : events) track.add(event);
        }
    }
}