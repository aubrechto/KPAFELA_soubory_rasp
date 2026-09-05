import os
import json
import msgpack
import shutil
import hashlib
import pandas
import numpy as np
import ms3
import argparse
import glob

def parse_cmd_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--song", help="Path to song/songs", required=False, default="songs/*", type=str)
    parser.add_argument("-o", "--output", help="Output directory", required=False, default="data/songs", type=str)
    parser.add_argument("-d", "--debug", help="Debug mode", required=False, action='store_true')
    return parser.parse_args()

def convert_seconds(seconds):
    seconds = seconds % (24 * 3600)
    # hour = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60

    # return "%d:%02d:%02d" % (hour, minutes, seconds)
    return "%02d:%02d" % (minutes, seconds)

def name_to_instrument(name):
    if name == "guitar" or name == "Guitar" or name == "Electric Guitar" or name == "Acoustic Guitar" or name == "Elektrick\u00e1 kytara":
        return "guitar"
    elif name == "bass" or name == "Baskytara":
        return "bass"
    elif name == "drums" or name == "Velk\u00e1 bic\u00ed souprava":
        return "drums"

def instrument_name(instrument):
    if instrument == "guitar":
        return "Guitar"
    elif instrument == "bass":
        return "Bass"
    elif instrument == "drums":
        return "Drums"

def kapfela_instruments():
    return ["guitar", "bass", "drums"]

def get_hash_fn(fn, ext=".json"):
    return hashlib.md5(os.path.splitext(os.path.basename(fn))[0].encode()).hexdigest()[0:16] + ext

def get_info(fn):
    song = ms3.Score(fn, level='ERROR' if not args.debug else 'INFO')

 #   print(json.dumps(song.mscx.metadata, indent=2))

    staffs = []
    for key, val in song.mscx.metadata["parts"].items():
        staffs.append(name_to_instrument(val["trackName"]))

    data = {}
    #data['artist'] = song.mscx.metadata["composer"]
    #data['title'] = song.mscx.metadata["workTitle"]
    fn_base = os.path.splitext(os.path.basename(fn))[0].split(" - ")
    data['artist'] = fn_base[0]
    data['title'] = fn_base[1]

    # tempo
    for chord in song.mscx.chords().itertuples():
        if chord.event == "Tempo":
            data['tempo'] = int(chord.qpm)

    # estimate song duration
    duration_total = song.mscx.metadata["length_qb"] * 60.0 / data['tempo']
    # print(data['tempo'])
    # print(duration_total)
    data['duration'] = duration_total
    data['duration_str'] = convert_seconds(duration_total)
    data['measures'] = len(song.mscx.measures())

    data['instruments'] = []
    for staff_id in song.mscx.staff_ids:
        data['instruments'].append(staffs[staff_id - 1])
    
    return data

def get_song(fn, instrument):
    """
    Returns the dictionary of tabs for single instrument
    """

    song = ms3.Score(fn)

    staffs = []
    for key, val in song.mscx.metadata["parts"].items():
        staffs.append(name_to_instrument(val["trackName"]))

    data = {}
    #data['artist'] = song.mscx.metadata["composer"]
    #data['title'] = song.mscx.metadata["workTitle"]
    fn_base = os.path.splitext(os.path.basename(fn))[0].split(" - ")
    data['artist'] = fn_base[0]
    data['title'] = fn_base[1]
    
    # tempo
    for chord in song.mscx.chords().itertuples():
        if chord.event == "Tempo":
            data['tempo'] = int(chord.qpm)

    # estimate song duration
    duration_total = song.mscx.metadata["length_qb"] * 60.0 / data['tempo']
    data['duration'] = duration_total
    data['duration_str'] = convert_seconds(duration_total)
    data['measures'] = len(song.mscx.measures())

    data['instruments'] = []
    for staff_id in song.mscx.staff_ids:
        data['instruments'].append(staffs[staff_id - 1])

    
    for staff_id in song.mscx.staff_ids:
        # skip unwanted instruments
        if staffs[staff_id-1] != instrument:
            continue
        # init chord id
        chord_id = -1

        instrument = {}
        instrument['id'] = staffs[staff_id-1]
        instrument['name'] = instrument_name(staffs[staff_id-1])
        instrument['m'] = []

        iii = 0 
        for measure in song.mscx.measures().itertuples():
            filter = f'staff == {staff_id} and mn == {measure.mn}'
            measures = []
            for item in song.mscx.notes_and_rests().query(filter).itertuples():
                # store chord id
                if pandas.isna(item.chord_id) or item.chord_id != chord_id:
                    if pandas.isna(item.chord_id):
                        chord_id = -1
                    else:
                        chord_id = item.chord_id

                    # init chord
                    chord = {}
                    chord['n'] = []
                    # add to beats
                    measures.append(chord)                

                # new item
                n = {}        
                iii += 1  

                # add begin and duration to chords
                # start in quarterbeats (total)
                # if 'b' not in chord:
                #     chord['b'] = float(item.quarterbeats)
                # duration in quarterbeats
                # if 'd' not in chord:
                #     chord['d'] = float(item.duration_qb)                                       
                
                if 'b' not in chord:
                     chord['b'] = str(float(item.quarterbeats)) + ":" + str(float(item.duration_qb))  
                
                if pandas.isna(item.chord_id):
                    # rest
                    pass                    
                else:
                    # tied
                    tied = '2' if pandas.isna(item.tied) else int(item.tied) # 2 means not tied                        
            
                    # notes
                    if instrument['id'] == "guitar" or instrument['id'] == "bass":
                        if not args.debug:
                            # note (midi, string, fret)
                            note = change_note(instrument['id'], item.name)
                            # n = {'m': int(item.midi), 's': note[0], 'f': note[1], 't': tied}
                            n = {'m': str(item.midi) + ":" + str(note[0]) + ":" + str(note[1]) + ":" + str(tied)}
                        else:
                            # note (name, midi, string, fret)
                            note = change_note(instrument['id'], item.name)
                            # n = {'n': item.name, 'm': int(item.midi), 's': note[0], 'f': note[1], 't': tied}
                            n = {'m': str(item.name) + ":" + str(item.midi) + ":" + str(note[0]) + ":" + str(note[1]) + ":" + str(tied)}
                    elif instrument['id'] == "drums":
                        if not args.debug:
                            # note (midi)
                            # n = {'m': int(item.midi)}
                            n = {'m': str(item.midi) + ":::"}
                        else:
                            # note (name, midi)
                            # n = {'n': item.name, 'm': int(item.midi)}
                            n = {'m': str(item.name) + ":" + str(item.midi) + ":::"}
                    else:
                        n = {}
                        
                    chord['n'].append(n)

            instrument['m'].append(measures)

        data['music'] = instrument

    return data




def change_note(instrument, note_name):
    """
    Change non-playable note if it's possible
    """
    guitar_notes = {
        'E2': (6, 0),
        'E#2': (6, 1),
        'F2': (6, 1),
        'F#2': (6, 2),
        'Gb2': (6, 2),
        'G2': (6, 3),
        'G#2': (6, 4),
        'Ab2': (6, 4),
        'A2': (5, 0),
#        'A2': (6, 5),
        'A#2': (5, 1),
        'Bb2': (5, 1),
        'B2': (5, 2),
        'C3': (5, 3),
        'C#3': (5, 4),
        'Db3': (5, 4),
        'D3': (4, 0),
#        'D3': (5, 5),
        'D#3': (4, 1),
        'Eb3': (4, 1),
        'E3': (4, 2),
        'F3': (4, 3),
        'F#3': (4, 4),
        'Gb3': (4, 4),
        'G3': (3, 0),
#        'G3': (4, 5),
        'G#3': (3, 1),
        'Ab3': (3, 1),
        'A3': (3, 2),
        'A#3': (3, 3),
        'Bb3': (3, 3),
        'B3': (2, 0),
#        'B3': (3, 5),
        'C4': (2, 1),
        'C#4': (2, 2),
        'Db4': (2, 2),
        'D4': (2, 3),
        'D#4': (2, 4),
        'Eb4': (2, 4),
        'E4': (1, 0),
#        'E4': (2, 5),
        'F4': (1, 1),
        'F#4': (1, 2),
        'Gb4': (1, 2),
        'G4': (1, 3),
        'G#4': (1, 4),
        'Ab4': (1, 4),
        'A4': (1, 5),
        'A#4': (1, 6),
        'Bb4': (1, 6),
        'B4': (1, 7),
        'C5': (1, 8),
        'C#5': (1, 9),
        'Db5': (1, 9),
        'D5': (1, 10),
        'D#5': (1, 11),
        'Eb5': (1, 11),
        'E5': (1, 12)
    }
    bass_notes = {
        'E1': (4, 0),
        'E#1': (4, 1),
        'F1': (4, 1),
        'F#1': (4, 2),
        'Gb1': (4, 2),
        'G1': (4, 3),
        'G#1': (4, 4),
        'Ab1': (4, 4),
        'A1': (3, 0),
        'A#1': (3, 1),
        'Bb1': (3, 1),
        'B1': (3, 2),
        'C2': (3, 3),
        'C#2': (3, 4),
        'Db2': (3, 4),
        'D2': (2, 0),
        'D#2': (2, 1),
        'Eb2': (2, 1),
        'E2': (2, 2),
        'F2': (2, 3),
        'F#2': (2, 4),
        'Gb2': (2, 4),
        'G2': (1, 0),
        'G#2': (1, 1),
        'Ab2': (1, 1),
        'A2': (1, 2),
        'A#2': (1, 3),
        'Bb2': (1, 3),
        'B2': (1, 4),
        'C3': (1, 5),
        'C#3': (1, 6),
        'Db3': (1, 6),
        'D3': (1, 7),
        'D#3': (1, 8),
        'Eb3': (1, 8),
        'E3': (1, 9),
        'F3': (1, 10),
        'F#3': (1, 11),
        'Gb3': (1, 11),
        'G3': (1, 12),
    }

    false_note = [-1, -1]
    if instrument == 'guitar' and note_name in guitar_notes:
        false_note = guitar_notes[note_name]
    elif instrument == 'bass' and note_name in bass_notes:
        false_note = bass_notes[note_name]
    return false_note


if __name__ == '__main__':
    args = parse_cmd_args()
    songs = args.song
    instrument_dir = args.output
    
    if args.debug:
        print("Debug mode ON")
    
    # remove dir
    if os.path.exists(f"{instrument_dir}"):
        shutil.rmtree(instrument_dir)

    # Store all three instrument tracks in one file. The ESP selects one track
    # at runtime using KAPFELA_INSTRUMENT_ID.
    os.makedirs(instrument_dir, exist_ok=True)

    for filename in glob.glob(songs):
        print("Generating: {}".format(filename))
        if filename.endswith(".mscz"):
            info = get_info(f'{filename}')

            # save info to json
            filename_base = os.path.splitext(filename)[0]
            print(f"{filename_base}")
            with open(f"{instrument_dir}/{get_hash_fn(filename_base)}", 'w') as f:
                f.write(json.dumps(info, sort_keys=True, ensure_ascii=False))

            tracks = {}
            for instrument in kapfela_instruments():
                track = get_song(f'{filename}', instrument)
                tracks[instrument] = track.get("music", {
                    "id": instrument,
                    "name": instrument_name(instrument),
                    "m": []
                })

            bundle = dict(info)
            bundle["tracks"] = tracks

            with open(f"{instrument_dir}/{get_hash_fn(filename_base, ext='.msg')}", "wb") as f:
                f.write(msgpack.packb(bundle, use_bin_type=True))

            with open(f"{instrument_dir}/{get_hash_fn(filename_base)}", 'w') as f:
                f.write(json.dumps(info, sort_keys=True, ensure_ascii=False))
