"""Bounded audio previews and lossless, validated removal of audio tracks."""
import array
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PySide6.QtCore import QObject, QRunnable, Signal

from .video_export import CREATE_FLAGS, VideoExport, encoding, probe, signature, tool


class AudioWaveform(VideoExport):
    def run(self, path, expected, bins=2048):
        path = Path(path)
        self.check_cancelled()
        if signature(path) != expected:
            raise ValueError('Видео изменилось во время загрузки аудиограммы.')
        info = probe(path)
        self.check_cancelled()
        if not info['audio']:
            return (), info['duration']
        track = next((s for s in info['audio'] if s.get('disposition', {}).get('default')), info['audio'][0])
        channels = max(1, int(track.get('channels', 1)))
        rate = 16000
        samples_per_bin = max(1, math.ceil(info['duration'] * rate / bins))
        peaks = [0.] * bins
        frame = 0
        video_start = float(info['stream'].get('start_time') or 0)
        args = [tool('ffmpeg'), '-hide_banner', '-nostdin', '-v', 'error', '-copyts', '-i', str(path),
                '-map', '0:' + str(track['index']), '-vn', '-af',
                f'asetpts=PTS-{video_start:.9f}/TB,aresample={rate}:async=1:first_pts=0',
                '-t', str(info['duration']), '-ac', str(channels), '-f', 'f32le', 'pipe:1']
        try:
            with tempfile.TemporaryFile() as errors:
                self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors,
                                                stdin=subprocess.DEVNULL, creationflags=CREATE_FLAGS)
                if self.cancelled.is_set():
                    self.cancel()
                remainder = b''
                while True:
                    self.check_cancelled()
                    chunk = self.process.stdout.read(65536)
                    if not chunk:
                        break
                    chunk = remainder + chunk
                    size = len(chunk) // (4 * channels) * (4 * channels)
                    remainder = chunk[size:]
                    values = array.array('f')
                    values.frombytes(chunk[:size])
                    if sys.byteorder != 'little':
                        values.byteswap()
                    for i in range(0, len(values), channels):
                        bucket = min(bins - 1, frame // samples_per_bin)
                        peak = max((abs(v) for v in values[i:i + channels] if math.isfinite(v)), default=0.)
                        peaks[bucket] = max(peaks[bucket], min(1., peak))
                        frame += 1
                code = self.process.wait()
                self.check_cancelled()
                if code:
                    errors.seek(0)
                    raise OSError(errors.read(3000).decode('utf-8', errors='replace'))
            if signature(path) != expected:
                raise ValueError('Видео изменилось во время загрузки аудиограммы.')
            return tuple(peaks), info['duration']
        finally:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait()
                self.process.stdout.close()
                self.process = None


class RemoveAudio(VideoExport):
    def run(self, path, expected, progress=lambda value: None):
        path = Path(path)
        self.check_cancelled()
        if signature(path) != expected:
            raise ValueError('Исходное видео изменилось. Звук не удалён.')
        info = probe(path)
        self.check_cancelled()
        if not info['audio']:
            progress(100)
            return info
        mux, _ = encoding(path.suffix.lower())
        fd, name = tempfile.mkstemp(prefix='.mediacategorizer-muted-', suffix='.part', dir=path.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            args = [tool('ffmpeg'), '-hide_banner', '-nostdin', '-y', '-v', 'error', '-i', str(path),
                    '-map', '0:V', '-c:v', 'copy', '-an', '-map_metadata', '0', '-map_chapters', '0',
                    '-progress', 'pipe:1', '-nostats']
            if mux in ('mp4', 'mov'):
                args += ['-movflags', '+faststart']
            args += ['-f', mux, str(temporary)]
            with tempfile.TemporaryFile() as errors:
                self.check_cancelled()
                self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors,
                                                stdin=subprocess.DEVNULL, creationflags=CREATE_FLAGS,
                                                text=True, encoding='utf-8')
                if self.cancelled.is_set():
                    self.cancel()
                for line in self.process.stdout:
                    if line.startswith('out_time_us='):
                        try:
                            progress(min(94, max(0, int(float(line.split('=')[1]) / (info['duration'] * 1000000) * 94))))
                        except ValueError:
                            pass
                code = self.process.wait()
                self.process.stdout.close()
                self.check_cancelled()
                if code:
                    errors.seek(0, 2)
                    errors.seek(max(0, errors.tell() - 3000))
                    raise OSError(errors.read().decode('utf-8', errors='replace'))
            progress(96)
            result = probe(temporary)
            before, after = info['stream'], result['stream']
            if result['audio'] or abs(result['duration'] - info['duration']) > max(.25, info['duration'] * .01):
                raise OSError('Проверка длительности или удаления звука не пройдена.')
            for key in ('codec_name', 'width', 'height', 'avg_frame_rate'):
                if before.get(key) != after.get(key):
                    raise OSError('Параметры видеоряда изменились. Исходное видео сохранено.')
            if (info['width'], info['height']) != (result['width'], result['height']):
                raise OSError('Ориентация видео изменилась. Исходное видео сохранено.')
            self.check_cancelled()
            if signature(path) != expected:
                raise ValueError('Исходное видео изменилось во время обработки. Оно не заменено.')
            with temporary.open('r+b') as complete:
                os.fsync(complete.fileno())
            self.check_cancelled()
            os.replace(temporary, path)
            progress(100)
            return result
        finally:
            if self.process is not None and self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.process = None
            temporary.unlink(missing_ok=True)


class AudioTaskSignals(QObject):
    waveform = Signal(object, object, str)
    progress = Signal(int)
    finished = Signal(object, str)


class WaveformTask(QRunnable):
    def __init__(self, path, expected):
        super().__init__()
        self.path, self.expected = Path(path), expected
        self.job = AudioWaveform()
        self.signals = AudioTaskSignals()

    def run(self):
        try:
            result = self.job.run(self.path, self.expected)
            self.signals.waveform.emit((str(self.path), self.expected), result, '')
        except Exception as exc:
            self.signals.waveform.emit((str(self.path), self.expected), None, str(exc))


class RemoveAudioTask(QRunnable):
    def __init__(self, path, expected):
        super().__init__()
        self.path, self.expected = Path(path), expected
        self.job = RemoveAudio()
        self.signals = AudioTaskSignals()

    def run(self):
        try:
            result = self.job.run(self.path, self.expected, self.signals.progress.emit)
            self.signals.finished.emit(result, '')
        except Exception as exc:
            self.signals.finished.emit(None, str(exc))
