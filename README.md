# probabilistic-composition-generator
Using large and small vocabulary MIR datasets to create an HMM for chord progressions applicable to various genres, then using JFugue to build compositions to be used in automatic chord recognition tasks 

How to use:
python composer.py
$env:FOR_DISABLE_CONSOLE_CTRL_HANDLER = "1"   
javac -cp jfugue-5.0.9.jar HumanizedMidiRenderer.java
java -cp ".;jfugue-5.0.9.jar" HumanizedMidiRenderer 

should get a gen folder, itself containing a folder with chord labels, a folder with midi_output, and a generated_scores.txt from which the outputs are actually built.

to do:
- see if these generated scores actually serve as useful training data
- update any musical composition mechanisms if necessary
- turn midis into wavs with FluidSynth and add audio artifacts/noise
