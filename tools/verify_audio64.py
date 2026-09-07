"""Verify generated audio reaches Qt's output pipeline across actual video slot switches."""
import os,sys,time,tempfile,subprocess,json,array
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM']='windows'
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QAudioBufferOutput,QAudioFormat,QMediaPlayer,QMediaDevices
from media_categorizer_v4_5 import MediaCategorizer
from media_categorizer.video_export import tool,CREATE_FLAGS

class TestWindow(MediaCategorizer):
    def _preload_window(self): pass
    def _preload_video_window(self): pass

app=QApplication([])
def wait(predicate,timeout=10):
    until=time.monotonic()+timeout
    while not predicate() and time.monotonic()<until:
        app.processEvents();time.sleep(.01)
    assert predicate(),'Playback condition timed out'

with tempfile.TemporaryDirectory() as directory:
    folder=Path(directory)
    os.environ['APPDATA']=str(folder/'config')
    for i,duration in enumerate((2,.35,1)):
        subprocess.run([tool('ffmpeg'),'-v','error','-nostdin','-y','-f','lavfi','-i',f'color=size=160x90:rate=30:duration={duration}', '-f','lavfi','-i',f'sine=frequency={440+i*220}:duration={duration}','-c:v','libx264','-c:a','aac',str(folder/f'{i}.mp4')],check=True,creationflags=CREATE_FLAGS)
    main=TestWindow()
    main.setAttribute(Qt.WA_ShowWithoutActivating)
    main.setWindowFlag(Qt.WindowDoesNotAcceptFocus,True)
    main.showNormal()
    buffers=[0,0]
    outputs=[]
    def received(index,buffer):
        if buffer.isValid() and buffer.byteCount():
            raw=buffer.constData()
            if hasattr(raw,'setsize'):raw.setsize(buffer.byteCount())
            data=bytes(raw)
            if any(data):buffers[index]+=1
    for index,slot in enumerate(main.video_slots):
        output=QAudioBufferOutput(main)
        output.audioBufferReceived.connect(lambda buffer,i=index:received(i,buffer))
        slot['player'].setAudioBufferOutput(output)
        outputs.append(output)
    try:
        main.load_folder(folder)
        main.loop_btn.setChecked(True)
        wait(lambda:buffers[main.active_video_slot]>0)
        report=[]
        for target in (1,2,0,1,2,0):
            path=folder/f'{target}.mp4'
            main._preload_video(path,{path})
            index=main._find_video_slot(path)
            wait(lambda:not main.video_slots[index]['preview'].isNull())
            before=buffers[index]
            main.current_index=main.files.index(path)
            main.show_current_file()
            main.sound_btn.setChecked(False)
            assert main.video_slots[index]['audio'].isMuted()
            main.sound_btn.setChecked(True)
            wait(lambda:buffers[index]>before and main.video_slots[index]['player'].position()>=30 and main.video_slots[index]['player'].playbackState()==QMediaPlayer.PlayingState)
            slot=main.video_slots[index]
            assert not slot['audio'].isMuted()
            assert slot['audio'].volume()==1
            assert slot['player'].audioOutput() is slot['audio']
            assert slot['player'].activeAudioTrack()>=0
            assert all(other['audio'].isMuted() for i,other in enumerate(main.video_slots) if i!=index)
            report.append(dict(file=path.name,buffers=buffers[index]-before,position=slot['player'].position(),muted=slot['audio'].isMuted()))
        main._refresh_audio_devices()
        assert main.video_slots[main.active_video_slot]['audio'].device()==QMediaDevices.defaultAudioOutput()
        print(json.dumps(dict(output_device_available=not QMediaDevices.defaultAudioOutput().isNull(),switches=report)),flush=True)
    finally:
        main.stop_all_video()
        main.close()
        main.thread_pool.waitForDone()
        app.processEvents()
