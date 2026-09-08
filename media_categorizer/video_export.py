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
    cuts: tuple = ()
    rotation: int = 0
    mirror: bool = False
    crop_ratio: str = ''
    normalize: bool = False
    crop_rect: tuple = ()

    def geometry(self, info):
        width, height = info['width'], info['height']
        if self.rotation % 180:
            width, height = height, width
        if self.crop_rect:
            width, height = width*self.crop_rect[2], height*self.crop_rect[3]
        elif self.crop_ratio:
            a, b = map(int, self.crop_ratio.split(':'))
            width, height = min(width, height*a/b), min(height, width*b/a)
        return max(2, int(width)//2*2), max(2, int(height)//2*2)

    def segments(self, start, end):
        cursor, result = start, []
        for left, right in sorted(self.cuts):
            if left >= end or right <= start:
                continue
            left, right = max(start, left), min(end, right)
            if right <= cursor:
                continue
            if left > cursor:
                result.append((cursor, left))
            cursor = max(cursor, right)
        if cursor < end:
            result.append((cursor, end))
        return [(a, b) for a, b in result if b-a >= .001]

    def dimensions(self, info):
        source_width, source_height = self.geometry(info)
        if self.resolution == 'source':
            return source_width, source_height
        if self.resolution not in ('720', '1080'):
            raise ValueError('Неизвестное разрешение.')
        short, long = (720, 1280) if self.resolution == '720' else (1080, 1920)
        if self.crop_rect:
            scale=min(long/max(source_width,source_height),short/min(source_width,source_height))
            return max(2,int(source_width*scale)//2*2),max(2,int(source_height*scale)//2*2)
        if self.crop_ratio == '1:1':
            return short, short
        return (short, long) if source_height > source_width else (long, short)

    def crop_geometry(self, info):
        width,height = info['width'],info['height']
        if self.rotation % 180: width,height = height,width
        cw,ch = self.geometry(info)
        if self.crop_rect:
            x,y = int(width*self.crop_rect[0])//2*2,int(height*self.crop_rect[1])//2*2
        else:
            x,y = int((width-cw)/2)//2*2,int((height-ch)/2)//2*2
        return cw,ch,x,y

    def validate(self):
        if self.crop_rect:
            if len(self.crop_rect)!=4 or not all(math.isfinite(v) for v in self.crop_rect):
                raise ValueError('Неверная рамка кадрирования.')
            x,y,w,h=self.crop_rect
            if min(x,y)<0 or min(w,h)<=0 or x+w>1.000001 or y+h>1.000001:
                raise ValueError('Рамка выходит за границы видео.')
        if self.fps not in (0, 24, 25, 30, 60) or self.quality not in ('high', 'balanced', 'compact'):
            raise ValueError('Неверные параметры экспорта.')
        if self.rotation not in (0, 90, 180, 270) or self.crop_ratio not in ('', '16:9', '9:16', '1:1'):
            raise ValueError('Неверные параметры кадрирования.')
        for left, right in self.cuts:
            if not all(math.isfinite(v) for v in (left, right)) or left < 0 or right <= left:
                raise ValueError('Неверные границы удаляемого фрагмента.')

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
        segments = options.segments(start, end)
        duration = sum(b-a for a, b in segments)
        if duration < 1/30:
            raise ValueError('После удаления фрагментов должен остаться хотя бы один кадр.')
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
            filters = 'scale=trunc(iw*sar/2)*2:ih,setsar=1'
            if options.rotation == 90:
                filters += ',transpose=clock'
            elif options.rotation == 180:
                filters += ',hflip,vflip'
            elif options.rotation == 270:
                filters += ',transpose=cclock'
            if options.mirror:
                filters += ',hflip'
            if options.crop_ratio or options.crop_rect:
                cw,ch,cx,cy = options.crop_geometry(info)
                filters += f',crop={cw}:{ch}:{cx}:{cy}'
            filters += f',scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1'
            if options.fps:
                filters += f',fps={options.fps}'
            graph, inputs = [], []
            count, audio_count = len(segments), len(info['audio'])
            # Independent trim branches preserve every audio stream, including silent videos.
            streams = [('v', info['stream']['index'])] + [('a'+str(i), s['index']) for i, s in enumerate(info['audio'])]
            for kind, index in streams:
                split = 'split' if kind == 'v' else 'asplit'
                if count > 1:
                    graph.append(f'[0:{index}]{split}={count}' + ''.join(f'[{kind}src{i}]' for i in range(count)))
                for i, (left, right) in enumerate(segments):
                    source = f'{kind}src{i}' if count > 1 else f'0:{index}'
                    trim, pts = ('trim', 'setpts') if kind == 'v' else ('atrim', 'asetpts')
                    graph.append(f'[{source}]{trim}=start={left-start:.6f}:end={right-start:.6f},{pts}=PTS-STARTPTS[{kind}seg{i}]')
            for i in range(count):
                inputs.extend([f'[vseg{i}]'] + [f'[a{j}seg{i}]' for j in range(audio_count)])
            graph.append(''.join(inputs) + f'concat=n={count}:v=1:a={audio_count}[joinedv]' + ''.join(f'[joineda{j}]' for j in range(audio_count)))
            graph.append(f'[joinedv]{filters}[outv]')
            maps = ['-map', '[outv]']
            for j in range(audio_count):
                sound = ('loudnorm=I=-16:TP=-1.5:LRA=11,' if options.normalize else '') + f'volume={volume:.6f}'
                if options.normalize:
                    sound += ',alimiter=limit=0.89:level=false:latency=true'
                graph.append(f'[joineda{j}]{sound}[outa{j}]')
                maps += ['-map', f'[outa{j}]']
            args = [tool('ffmpeg'), '-hide_banner', '-nostdin', '-y', '-v', 'error', '-ss', f'{start:.6f}', '-i', str(path),
                    '-t', f'{duration:.6f}', '-filter_complex', ';'.join(graph), *maps, *codecs, '-pix_fmt', 'yuv420p',
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
                            progress(min(95, max(0, int(float(line.split('=')[1]) / (duration*1000000)*95))))
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
            if abs(result['duration']-duration) > max(.25, duration*.01) or len(result['audio']) != len(info['audio']):
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
