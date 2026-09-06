"""Cancellable FFmpeg export; original is replaced only after validation."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from fractions import Fraction

CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def tool(name):
    directory = Path(sys.executable).parent / 'ffmpeg' if getattr(sys, 'frozen', False) else Path(__file__).parent / 'assets/ffmpeg'
    path = directory / (name + '.exe')
    if not path.is_file():
        raise OSError('Не найден кодировщик ' + name + '. Установите полный пакет обновления программы.')
    return str(path)


def signature(path):
    stat = Path(path).stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def probe(path):
    try:
        result = subprocess.run([tool('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                                capture_output=True, timeout=30, creationflags=CREATE_FLAGS)
    except subprocess.TimeoutExpired as exc:
        raise OSError("Не удалось прочитать параметры видео за 30 секунд.") from exc
    if result.returncode:
        raise OSError(result.stderr.decode('utf-8', errors='replace')[-1500:])
    data = json.loads(result.stdout)
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    if not videos:
        raise ValueError('В файле нет видеодорожки.')
    video = videos[0]
    duration = float(video.get('duration') or data.get('format', {}).get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Не удалось определить длительность видео.')
    rotation = float(video.get('tags', {}).get('rotate', 0))
    for side in video.get('side_data_list', []):
        rotation = float(side.get('rotation', rotation))
    width, height = int(video['width']), int(video['height'])
    sar = video.get('sample_aspect_ratio', '1:1')
    try:
        width *= float(Fraction(sar.replace(':', '/')))
    except (ValueError, ZeroDivisionError):
        pass
    if round(rotation) % 180:
        width, height = height, width
    return dict(duration=duration, width=width, height=height, stream=video,
                audio=[s for s in data['streams'] if s.get('codec_type') == 'audio'])


def encoding(suffix):
    if suffix in ('.mp4', '.m4v', '.mov', '.mkv', '.avi'):
        mux = {'.mov':'mov', '.mkv':'matroska', '.avi':'avi'}.get(suffix, 'mp4')
        return mux, ['-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-c:a', 'aac', '-b:a', '192k']
    if suffix == '.webm':
        return 'webm', ['-c:v', 'libvpx-vp9', '-deadline', 'good', '-cpu-used', '4', '-crf', '30', '-b:v', '0', '-c:a', 'libopus', '-b:a', '160k']
    if suffix == '.wmv':
        return 'asf', ['-c:v', 'wmv2', '-b:v', '5M', '-c:a', 'wmav2', '-b:a', '192k']
    if suffix in ('.mpeg', '.mpg'):
        return 'mpeg', ['-c:v', 'mpeg2video', '-q:v', '3', '-c:a', 'mp2', '-b:a', '192k']
    raise ValueError('Этот контейнер пока не поддерживается редактором.')


@dataclass(frozen=True)
class ExportOptions:
    resolution: str = '720'
    fps: int = 30
    quality: str = 'balanced'

    def dimensions(self, info):
        if self.resolution == 'source':
            return max(2, round(info['width']/2)*2), max(2, round(info['height']/2)*2)
        if self.resolution not in ('720', '1080'):
            raise ValueError('Неизвестное разрешение.')
        short, long = (720, 1280) if self.resolution == '720' else (1080, 1920)
        return (short, long) if info['height'] > info['width'] else (long, short)

    def validate(self):
        if self.fps not in (0, 24, 25, 30, 60) or self.quality not in ('high', 'balanced', 'compact'):
            raise ValueError('Неверные параметры экспорта.')

    def estimate_bytes(self, info, duration):
        width, height = self.dimensions(info)
        try:
            fps = self.fps or float(Fraction(info['stream'].get('avg_frame_rate') or '30/1')) or 30
        except (ValueError, ZeroDivisionError):
            fps = 30
        bitrate = width*height*fps*{'high':.12, 'balanced':.08, 'compact':.045}[self.quality]
        return duration*(bitrate+192000*len(info['audio']))/8


class ExportCancelled(Exception):
    pass


class VideoExport:
    def __init__(self):
        self.cancelled = threading.Event()
        self.process = None

    def cancel(self):
        self.cancelled.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise ExportCancelled('Сохранение отменено. Исходное видео не изменено.')

    def run(self, path, start, end, volume, expected, progress=lambda value: None, options=None):
        path = Path(path)
        options = options or ExportOptions()
        options.validate()
        if not all(math.isfinite(v) for v in (start, end, volume)) or start < 0 or end <= start or not 0 <= volume <= 2:
            raise ValueError('Проверьте начало, конец и громкость видео.')
        self.check_cancelled()
        if signature(path) != expected:
            raise ValueError('Исходное видео изменилось. Откройте редактор заново.')
        info = probe(path)
        if end > info['duration'] + .05 or end-start < 1/30:
            raise ValueError('Выберите отрезок не короче одного кадра в пределах видео.')
        width, height = options.dimensions(info)
        mux, codecs = encoding(path.suffix.lower())
        if "-crf" in codecs:
            codecs[codecs.index("-crf")+1] = str(({"high":18,"balanced":20,"compact":28} if mux != "webm" else {"high":24,"balanced":30,"compact":38})[options.quality])
        if "-q:v" in codecs:
            codecs[codecs.index("-q:v")+1] = str({"high":2,"balanced":3,"compact":7}[options.quality])
        if mux == "asf":
            codecs[codecs.index("-b:v")+1] = {"high":"8M","balanced":"5M","compact":"2M"}[options.quality]
        fd, name = tempfile.mkstemp(prefix='.mediacategorizer-video-', suffix='.part', dir=path.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            filters = f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1'
            if options.fps:
                filters += f',fps={options.fps}'
            args = [tool('ffmpeg'), '-hide_banner', '-nostdin', '-y', '-v', 'error', '-ss', f'{start:.6f}', '-i', str(path),
                    '-t', f'{end-start:.6f}', '-map', '0:' + str(info['stream']['index']), '-map', '0:a?',
                    '-vf', filters, '-af', f'volume={volume:.6f}', *codecs, '-pix_fmt', 'yuv420p',
                    '-metadata:s:v:0', 'rotate=0', '-fps_mode', 'cfr' if options.fps else 'vfr', '-progress', 'pipe:1', '-nostats']
            if mux in ('mp4', 'mov'):
                args += ['-movflags', '+faststart']
            args += ['-f', mux, str(temporary)]
            with tempfile.TemporaryFile() as errors:
                self.check_cancelled()
                self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors, stdin=subprocess.DEVNULL,
                                                creationflags=CREATE_FLAGS, text=True, encoding='utf-8')
                if self.cancelled.is_set():
                    self.cancel()
                for line in self.process.stdout:
                    if line.startswith('out_time_us='):
                        try:
                            progress(min(95, max(0, int(float(line.split('=')[1]) / ((end-start)*1000000)*95))))
                        except ValueError:
                            pass
                code = self.process.wait()
                self.process.stdout.close()
                self.check_cancelled()
                if code:
                    errors.seek(0, 2)
                    errors.seek(max(0, errors.tell()-3000))
                    raise OSError(errors.read().decode('utf-8', errors='replace'))
            progress(96)
            result = probe(temporary)
            fps = float(Fraction(result['stream']['avg_frame_rate']))
            if (result['stream']['width'], result['stream']['height']) != (width, height) or (options.fps and abs(fps-options.fps) > .01):
                raise OSError('Проверка готового видео не пройдена: разрешение или частота кадров.')
            if abs(result['duration']-(end-start)) > max(.25, (end-start)*.01) or len(result['audio']) != len(info['audio']):
                raise OSError('Проверка длительности или звуковых дорожек не пройдена.')
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
            process = self.process
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            self.process = None
            temporary.unlink(missing_ok=True)
