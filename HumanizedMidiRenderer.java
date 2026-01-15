import org.jfugue.pattern.Pattern;
import org.jfugue.player.Player;
import org.jfugue.midi.MidiFileManager;
import javax.sound.midi.*;
import java.io.*;
import java.util.Random;

public class HumanizedMidiRenderer {

    // CONFIGURATION
    private static final String INPUT_FILE = "gen/generated_scores.txt";
    private static final String OUTPUT_DIR = "gen/midi_output";
    // How "messy" is the player? (Standard Deviation in ticks)
    // At 120 BPM with default resolution, 15 ticks is roughly 15-20ms of slop.
    private static final int JITTER_TICKS = 15; 

    public static void main(String[] args) {
        File dir = new File(OUTPUT_DIR);
        if (!dir.exists()) dir.mkdirs();

        try (BufferedReader br = new BufferedReader(new FileReader(INPUT_FILE))) {
            String line;
            StringBuilder songBuffer = new StringBuilder();
            String currentSongName = "";
            boolean recording = false;

            System.out.println("--- Starting Batch Renderer ---");

            while ((line = br.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty()) continue;

                if (line.startsWith("START_SONG_")) {
                    currentSongName = line;
                    songBuffer.setLength(0); // Clear buffer
                    recording = true;
                } else if (line.startsWith("END_SONG")) {
                    if (recording) {
                        processSong(songBuffer.toString(), currentSongName);
                        recording = false;
                    }
                } else {
                    if (recording) {
                        songBuffer.append(line).append(" ");
                    }
                }
            }
            System.out.println("--- Rendering Complete ---");
            
        } catch (IOException e) {
            e.printStackTrace();
        }
    }

    private static void processSong(String jfugueString, String name) {
        try {
            // 1. Convert JFugue String -> MIDI Sequence
            Pattern pattern = new Pattern(jfugueString);
            Sequence sequence = new Player().getSequence(pattern);

            // 2. Apply "Sim2Real" Humanization
            humanizeSequence(sequence);

            // 3. Save to Disk
            File file = new File(OUTPUT_DIR, name + ".mid");
            MidiSystem.write(sequence, 1, file);
            
            // Simple progress indicator
            System.out.println("Rendered: " + name);

        } catch (Exception e) {
            System.err.println("Error rendering " + name + ": " + e.getMessage());
        }
    }

    private static void humanizeSequence(Sequence sequence) {
        Random random = new Random();

        for (Track track : sequence.getTracks()) {
            for (int i = 0; i < track.size(); i++) {
                MidiEvent event = track.get(i);
                MidiMessage msg = event.getMessage();

                // Only jitter Note On/Off events
                if (msg instanceof ShortMessage) {
                    ShortMessage sm = (ShortMessage) msg;
                    int cmd = sm.getCommand();

                    if (cmd == ShortMessage.NOTE_ON || cmd == ShortMessage.NOTE_OFF) {
                        long currentTick = event.getTick();

                        // Calculate Gaussian Jitter (Bell Curve)
                        // Most notes are close to the beat, some are far off.
                        double jitter = random.nextGaussian() * JITTER_TICKS;

                        long newTick = currentTick + (long) jitter;
                        
                        // Prevent negative time
                        if (newTick < 0) newTick = 0;
                        
                        event.setTick(newTick);
                    }
                }
            }
        }
    }
}