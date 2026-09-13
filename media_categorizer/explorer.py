"""Themed tabbed file browser. Filesystem enumeration runs in QFileSystemModel."""
import os
import ctypes
from pathlib import Path
from PySide6.QtCore import (Qt, QDir, QUrl, QSize, QSettings, QStandardPaths,
    QSortFilterProxyModel, QMimeData, QThread, Signal, QFile, QItemSelectionModel, QObject, QRunnable, QThreadPool)
from PySide6.QtGui import QAction, QKeySequence, QDesktopServices, QImageReader, QPixmap, QIcon, QImage
from PySide6.QtWidgets import (QFileSystemModel, QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLineEdit, QLabel, QTreeView, QListView, QListWidget, QListWidgetItem,
    QTabWidget, QStackedWidget, QSplitter, QWidget, QMenu, QInputDialog,
    QMessageBox, QAbstractItemView, QHeaderView, QToolButton, QTabBar, QPushButton)
from .ui import IconButton, COLORS, THEME_NAMES, icon
from .settings import app_config_dir
from .constants import SUPPORTED_EXTENSIONS, IMAGE_EXTENSIONS
from .file_operations import rename_no_replace


def valid_name(name):
    if (not name.strip() or name in ('.','..') or name.endswith(('.', ' ')) or
        any(ord(c)<32 or c in '<>:"/\\|?*' for c in name) or
        name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',
            *(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}):
        raise ValueError('Недопустимое имя файла или папки.')
    return name


def shell_transfer(paths, destination, move=False):
    """Windows handles recursive folder transfers and collision renaming."""
    from ctypes import wintypes
    if os.name!='nt':raise OSError('Эта операция доступна на Windows.')
    target=Path(destination).absolute()
    if not target.is_dir():raise OSError('Папка назначения недоступна.')
    paths=[str(Path(p).absolute()) for p in paths]
    for value in paths:
        source=Path(value)
        if not source.exists():raise OSError(f'Источник недоступен: {source}')
        if source.is_dir() and (target.resolve()==source.resolve() or source.resolve() in target.resolve().parents):
            raise ValueError('Нельзя скопировать папку внутрь неё самой.')
        if move and source.parent.resolve()==target.resolve():raise ValueError('Элемент уже находится в этой папке.')
    if not paths:return
    class Operation(ctypes.Structure):
        _fields_=[('hwnd',wintypes.HWND),('wFunc',wintypes.UINT),('pFrom',wintypes.LPCWSTR),
            ('pTo',wintypes.LPCWSTR),('flags',wintypes.WORD),('aborted',wintypes.BOOL),
            ('mapping',ctypes.c_void_p),('title',wintypes.LPCWSTR)]
    sources=ctypes.create_unicode_buffer('\0'.join(paths)+'\0\0')
    destination_buffer=ctypes.create_unicode_buffer(str(target)+'\0\0')
    operation=Operation(None,1 if move else 2,ctypes.cast(sources,wintypes.LPCWSTR),
        ctypes.cast(destination_buffer,wintypes.LPCWSTR),0x0004|0x0008|0x0200|0x0400,False,None,None)
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    call=shell.SHFileOperationW;call.argtypes=[ctypes.POINTER(Operation)];call.restype=ctypes.c_int
    result=call(ctypes.byref(operation))
    if result or operation.aborted:
        raise OSError(f'Операция не завершена (код {result}). Часть элементов могла быть обработана; проверьте папку назначения.')


class TransferThread(QThread):
    def __init__(self,paths,destination,move,parent):
        super().__init__(parent);self.paths=paths;self.destination=destination;self.move=move;self.error=''
    def run(self):
        try:shell_transfer(self.paths,self.destination,self.move)
        except Exception as exc:self.error=str(exc)


class ThumbnailSignals(QObject):
    ready=Signal(str,object,QImage)


class ThumbnailTask(QRunnable):
    def __init__(self,path,state):
        super().__init__();self.path=path;self.state=state;self.signals=ThumbnailSignals()
    def run(self):
        reader=QImageReader(self.path);reader.setAutoTransform(True)
        size=reader.size()
        if size.isValid():reader.setScaledSize(size.scaled(160,120,Qt.KeepAspectRatio))
        image=reader.read()
        try:self.signals.ready.emit(self.path,self.state,image)
        except RuntimeError:pass  # Browser closed while decoding.


class BrowserModel(QFileSystemModel):
    def __init__(self,parent):
        super().__init__(parent);self.thumbnails={};self.pending={};self.folder_icon=QIcon()
    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if orientation==Qt.Horizontal and role==Qt.DisplayRole and section<4:
            return ('Имя','Размер','Тип','Дата изменения')[section]
        return super().headerData(section,orientation,role)
    def data(self,index,role=Qt.DisplayRole):
        if index.isValid() and role==Qt.DecorationRole and index.column()==0:
            if self.isDir(index) and not self.folder_icon.isNull():return self.folder_icon
            path=self.filePath(index)
            if Path(path).suffix.lower() in IMAGE_EXTENSIONS:
                info=self.fileInfo(index);state=(info.size(),info.lastModified().toMSecsSinceEpoch())
                cached=self.thumbnails.get(path)
                if cached and cached[0]==state:
                    if not cached[1].isNull():return cached[1]
                elif path not in self.pending:
                    task=ThumbnailTask(path,state);self.pending[path]=task
                    task.signals.ready.connect(self.thumbnail_ready);QThreadPool.globalInstance().start(task)
        if index.isValid() and role==Qt.DisplayRole and index.column()==2:
            return 'Папка' if self.isDir(index) else (Path(self.filePath(index)).suffix.lstrip('.').upper()+' — файл' or 'Файл')
        return super().data(index,role)
    def thumbnail_ready(self,path,state,image):
        self.pending.pop(path,None)
        self.thumbnails[path]=(state,QIcon(QPixmap.fromImage(image)) if not image.isNull() else QIcon())
        while len(self.thumbnails)>256:self.thumbnails.pop(next(iter(self.thumbnails)))
        index=self.index(path)
        if index.isValid():self.dataChanged.emit(index,index,[Qt.DecorationRole])


class FolderFilter(QSortFilterProxyModel):
    def __init__(self,parent):
        super().__init__(parent);self.folder='';self.query=''
        self.setSortCaseSensitivity(Qt.CaseInsensitive);self.setDynamicSortFilter(True)
    def filterAcceptsRow(self,row,parent):
        model=self.sourceModel()
        return (Path(model.filePath(parent))!=Path(self.folder) or not self.query or
                self.query.casefold() in model.fileName(model.index(row,0,parent)).casefold())
    def search(self,text):
        self.beginFilterChange();self.query=text;self.endFilterChange(QSortFilterProxyModel.Direction.Rows)


class FolderTab(QWidget):
    changed=Signal()
    opened=Signal(str)
    menuRequested=Signal(object)
    def __init__(self,folder,parent):
        super().__init__(parent);self.history=[];self.position=-1;self.folder=Path(folder)
        self.model=BrowserModel(self);self.model.setReadOnly(True)
        self.model.setFilter(QDir.AllEntries|QDir.NoDotAndDotDot|QDir.AllDirs)
        self.proxy=FolderFilter(self);self.proxy.setSourceModel(self.model)
        self.tree=QTreeView();self.tree.setRootIsDecorated(False);self.tree.setItemsExpandable(False)
        self.tree.setSortingEnabled(True);self.tree.setUniformRowHeights(True)
        self.tiles=QListView();self.tiles.setViewMode(QListView.IconMode);self.tiles.setResizeMode(QListView.Adjust)
        self.tiles.setMovement(QListView.Static);self.tiles.setWordWrap(True)
        self.tiles.setIconSize(QSize(64,64));self.tiles.setGridSize(QSize(132,116))
        self.stack=QStackedWidget();self.stack.addWidget(self.tree);self.stack.addWidget(self.tiles)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.addWidget(self.stack)
        for view in (self.tree,self.tiles):
            view.setModel(self.proxy);view.setSelectionMode(QAbstractItemView.ExtendedSelection)
            view.setEditTriggers(QAbstractItemView.NoEditTriggers);view.setContextMenuPolicy(Qt.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda p,v=view:self.menuRequested.emit(v.viewport().mapToGlobal(p)))
            view.activated.connect(lambda index:self.opened.emit(self.model.filePath(self.proxy.mapToSource(index.siblingAtColumn(0)))))
            view.selectionModel().selectionChanged.connect(lambda *_:self.changed.emit())
        self.tree.sortByColumn(0,Qt.AscendingOrder);self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0,QHeaderView.Stretch)
        for col,width in ((1,110),(2,140),(3,165)):self.tree.setColumnWidth(col,width)
        self.model.directoryLoaded.connect(lambda *_:self.changed.emit())
        self.proxy.rowsInserted.connect(lambda *_:self.changed.emit())
        self.navigate(folder)
    def view(self):return self.stack.currentWidget()
    def selected(self):return [Path(self.model.filePath(self.proxy.mapToSource(i))) for i in self.view().selectionModel().selectedRows(0)]
    def navigate(self,path,record=True):
        path=Path(os.path.abspath(os.path.expandvars(os.path.expanduser(str(path)))))
        if not path.is_dir():raise OSError('Папка не найдена: '+str(path))
        with os.scandir(path):pass
        self.folder=path;self.proxy.folder=str(path);self.proxy.search('')
        index=self.proxy.mapFromSource(self.model.setRootPath(str(path)))
        for view in (self.tree,self.tiles):view.setRootIndex(index);view.clearSelection()
        if record and (self.position<0 or self.history[self.position]!=path):
            self.history=self.history[:self.position+1]+[path];self.position=len(self.history)-1
        self.changed.emit()
    def travel(self,step):
        position=self.position+step
        if 0<=position<len(self.history):
            self.navigate(self.history[position],False);self.position=position;self.changed.emit()
    def set_mode(self,tiles):
        selected=self.view().selectionModel().selectedRows(0);self.stack.setCurrentIndex(int(tiles))
        self.view().clearSelection()
        for index in selected:self.view().selectionModel().select(index,QItemSelectionModel.Select|QItemSelectionModel.Rows)
        self.changed.emit()


class ExplorerDialog(QDialog):
    def __init__(self,main):
        super().__init__(main);self.main=main;self.worker=None;self.hidden=False
        self.setWindowTitle('Проводник · Media Categorizer')
        self.setWindowModality(Qt.WindowModal)
        self.setWindowFlags(self.windowFlags()|Qt.WindowMinimizeButtonHint|Qt.WindowMaximizeButtonHint)
        self.resize(1180,760)
        self.preferences=QSettings(str(app_config_dir()/'explorer.ini'),QSettings.IniFormat)
        self.tabs=QTabWidget();self.tabs.setDocumentMode(True);self.tabs.setTabsClosable(True);self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab);self.tabs.currentChanged.connect(self.sync)
        self.back=IconButton('chevron-left','Назад',compact=True);self.forward=IconButton('chevron-right','Вперёд',compact=True)
        self.up=IconButton('folder-open','На уровень выше',compact=True);self.reload=IconButton('rotate-cw','Обновить',compact=True)
        self.address=QLineEdit();self.address.setPlaceholderText('Путь к папке');self.address.setAccessibleName('Адрес папки')
        self.search=QLineEdit();self.search.setPlaceholderText('Поиск в текущей папке');self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(230);self.search.setMaximumWidth(300);self.search.setAccessibleName('Поиск в текущей папке')
        self.back.clicked.connect(lambda:self.travel(-1));self.forward.clicked.connect(lambda:self.travel(1))
        self.up.clicked.connect(lambda:self.navigate(self.current().folder.parent));self.reload.clicked.connect(self.refresh)
        self.address.returnPressed.connect(lambda:self.navigate(self.address.text()));self.search.textChanged.connect(self.filter_files)
        self.sidebar=QListWidget();self.sidebar.setMinimumWidth(170);self.sidebar.setMaximumWidth(240)
        self.sidebar.itemClicked.connect(lambda item:self.navigate(item.data(Qt.UserRole)) if item.data(Qt.UserRole) else None)
        self.status=QLabel();self.status.setObjectName('muted');self.status.setWordWrap(True)
        title=QLabel('Проводник');title.setObjectName('sectionTitle')
        self.new_tab=IconButton('plus','Новая вкладка',compact=True);self.new_tab.clicked.connect(lambda:self.add_tab(self.current().folder))
        self.theme=IconButton('moon','Тема',compact=True);menu=QMenu(self.theme)
        for key,label in THEME_NAMES.items():menu.addAction(label).triggered.connect(lambda checked=False,k=key:self.change_theme(k))
        self.theme.setMenu(menu)
        self.create=IconButton('file-plus','Создать папку');self.cut=IconButton('scissors','Вырезать',compact=True)
        self.copy=IconButton('copy','Копировать',compact=True);self.paste=IconButton('copy','Вставить',compact=True)
        self.rename=IconButton('pencil','Переименовать',compact=True);self.delete=IconButton('trash-2','В корзину',compact=True)
        self.view_button=IconButton('images','Вид');self.open_button=IconButton('play','Открыть в программе')
        self.open_button.setProperty('primary',True)
        self.create.clicked.connect(self.create_folder);self.cut.clicked.connect(lambda:self.copy_selection(True))
        self.copy.clicked.connect(lambda:self.copy_selection(False));self.paste.clicked.connect(self.paste_selection)
        self.rename.clicked.connect(self.rename_selection);self.delete.clicked.connect(self.trash_selection);self.open_button.clicked.connect(self.open_selected)
        view_menu=QMenu(self.view_button)
        view_menu.addAction('Таблица').triggered.connect(lambda:self.set_mode(False))
        view_menu.addAction('Крупные значки').triggered.connect(lambda:self.set_mode(True))
        view_menu.addSeparator();hidden=view_menu.addAction('Скрытые файлы');hidden.setCheckable(True);hidden.toggled.connect(self.show_hidden)
        self.view_button.setMenu(view_menu)
        layout=QVBoxLayout(self);layout.setSpacing(10)
        top=QHBoxLayout();top.addWidget(title);top.addStretch();top.addWidget(self.new_tab);top.addWidget(self.theme);layout.addLayout(top)
        navigation=QHBoxLayout()
        for widget in (self.back,self.forward,self.up,self.reload,self.address,self.search):navigation.addWidget(widget,1 if widget is self.address else 0)
        layout.addLayout(navigation);commands=QHBoxLayout()
        for widget in (self.create,self.cut,self.copy,self.paste,self.rename,self.delete):commands.addWidget(widget)
        commands.addStretch();commands.addWidget(self.view_button);commands.addWidget(self.open_button);layout.addLayout(commands)
        splitter=QSplitter();splitter.addWidget(self.sidebar);splitter.addWidget(self.tabs);splitter.setStretchFactor(1,1)
        layout.addWidget(splitter,1);layout.addWidget(self.status);self.populate_sidebar()
        initial=main.settings.get('last_folder') or str(Path.home())
        self.add_tab(initial if Path(initial).is_dir() else Path.home())
        self.set_mode(self.preferences.value('tiles',False,type=bool))
        geometry=self.preferences.value('geometry')
        if geometry:self.restoreGeometry(geometry)
        for keys,callback in [('Alt+Left',lambda:self.travel(-1)),('Alt+Right',lambda:self.travel(1)),
            ('Alt+Up',lambda:self.navigate(self.current().folder.parent)),('Ctrl+L',self.focus_address),('Ctrl+F',self.search.setFocus),
            ('Ctrl+T',lambda:self.add_tab(self.current().folder)),('Ctrl+W',lambda:self.close_tab(self.tabs.currentIndex())),
            ('F5',self.refresh),('Ctrl+C',lambda:self.copy_selection(False)),('Ctrl+X',lambda:self.copy_selection(True)),
            ('Ctrl+V',self.paste_selection),('F2',self.rename_selection),('Delete',self.trash_selection),
            ('Ctrl+Shift+N',self.create_folder)]:
            action=QAction(self);action.setShortcut(QKeySequence(keys));action.triggered.connect(callback);self.addAction(action)
        for button in self.findChildren(QPushButton):button.setAutoDefault(False);button.setDefault(False)
        QApplication.clipboard().dataChanged.connect(self.sync);self.refresh_theme()
    def current(self):return self.tabs.currentWidget()
    def focus_address(self):self.address.setFocus();self.address.selectAll()
    def editing_text(self):return isinstance(QApplication.focusWidget(),QLineEdit)
    def populate_sidebar(self):
        entries=[('Быстрый доступ',None),('Главная',str(Path.home()))]
        for label,kind in [('Рабочий стол',QStandardPaths.DesktopLocation),('Загрузки',QStandardPaths.DownloadLocation),
            ('Документы',QStandardPaths.DocumentsLocation),('Изображения',QStandardPaths.PicturesLocation),('Видео',QStandardPaths.MoviesLocation),('Музыка',QStandardPaths.MusicLocation)]:
            path=QStandardPaths.writableLocation(kind)
            if path and Path(path).is_dir():entries.append((label,path))
        entries.append(('Этот компьютер',None));entries.extend((d.absoluteFilePath(),d.absoluteFilePath()) for d in QDir.drives())
        for title,path in entries:
            item=QListWidgetItem(title);item.setData(Qt.UserRole,path)
            if not path:item.setFlags(Qt.NoItemFlags)
            self.sidebar.addItem(item)
    def refresh_theme(self):
        if not hasattr(self,'sidebar'):return
        c=COLORS[QApplication.instance().property('theme') or 'dark']
        self.sidebar.setStyleSheet(f'QListWidget {{ background: {c["bg"]}; border: none; }} QListWidget::item {{ padding: 10px 8px; border-radius: 6px; }} QListWidget::item:selected {{ background: {c["selected"]}; border-left: 3px solid {c["accent"]}; }}')
        for i in range(self.sidebar.count()):
            item=self.sidebar.item(i)
            if item.data(Qt.UserRole):item.setIcon(icon('folder-open',c['accent']))
        self.tabs.setStyleSheet('QTreeView::item { min-height: 30px; padding: 3px; } QListView::item { padding: 6px; }')
        for i in range(self.tabs.count()):
            tab=self.tabs.widget(i);tab.model.folder_icon=icon('folder-open',c['accent'])
            tab.tree.viewport().update();tab.tiles.viewport().update()
            button=self.tabs.tabBar().tabButton(i,QTabBar.ButtonPosition.RightSide)
            if isinstance(button,QToolButton):button.setIcon(icon('x',c['muted']))
    def change_theme(self,key):self.main.theme_actions[key].trigger();self.refresh_theme()
    def add_tab(self,folder):
        if self.worker:return
        try:tab=FolderTab(folder,self)
        except OSError as exc:self.error(exc);return
        tab.changed.connect(self.sync);tab.opened.connect(self.open_path);tab.menuRequested.connect(self.context_menu)
        index=self.tabs.addTab(tab,tab.folder.name or str(tab.folder));self.tabs.setCurrentIndex(index)
        close=QToolButton();close.setAutoRaise(True);close.setFixedSize(26,26);close.setToolTip('Закрыть вкладку')
        close.clicked.connect(lambda:self.close_tab(self.tabs.indexOf(tab)))
        self.tabs.tabBar().setTabButton(index,QTabBar.ButtonPosition.RightSide,close)
        self.show_hidden(self.hidden);self.sync();self.refresh_theme()
    def close_tab(self,index):
        if self.worker:return
        if self.tabs.count()==1:self.reject();return
        tab=self.tabs.widget(index);self.tabs.removeTab(index);tab.deleteLater();self.sync()
    def navigate(self,path):
        if self.worker:return
        try:self.current().navigate(path);self.address.setText(str(self.current().folder));self.search.clear();self.sync()
        except (OSError,ValueError) as exc:self.error(exc);self.sync()
    def travel(self,step):
        if self.worker:return
        try:self.current().travel(step);self.search.clear();self.sync()
        except OSError as exc:self.error(exc)
    def refresh(self):
        if self.current():self.navigate(self.current().folder)
    def filter_files(self,text):
        if self.current():self.current().proxy.search(text);self.sync_status()
    def set_mode(self,tiles):
        if self.current():self.current().set_mode(tiles)
        self.preferences.setValue('tiles',tiles)
    def show_hidden(self,visible):
        self.hidden=visible
        for i in range(self.tabs.count()):self.tabs.widget(i).model.setFilter(QDir.AllEntries|QDir.AllDirs|QDir.NoDotAndDotDot|(QDir.Hidden if visible else QDir.Filter(0)))
    def sync(self,*_):
        tab=self.current()
        if tab is None:return
        # Do not erase a path the user is currently typing on directoryLoaded.
        if not self.address.hasFocus():self.address.setText(str(tab.folder))
        self.search.blockSignals(True);self.search.setText(tab.proxy.query);self.search.blockSignals(False)
        self.tabs.setTabText(self.tabs.currentIndex(),tab.folder.name or str(tab.folder));self.tabs.setTabToolTip(self.tabs.currentIndex(),str(tab.folder))
        busy=self.worker is not None;selection=tab.selected()
        self.back.setEnabled(not busy and tab.position>0);self.forward.setEnabled(not busy and tab.position<len(tab.history)-1)
        self.up.setEnabled(not busy and tab.folder.parent!=tab.folder)
        for widget in (self.copy,self.cut,self.delete):widget.setEnabled(not busy and bool(selection))
        self.rename.setEnabled(not busy and len(selection)==1);self.open_button.setEnabled(not busy and bool(selection))
        mime=QApplication.clipboard().mimeData();self.paste.setEnabled(not busy and mime is not None and mime.hasUrls())
        for widget in (self.create,self.address,self.reload,self.new_tab,self.sidebar):widget.setEnabled(not busy)
        self.sync_status()
    def sync_status(self):
        tab=self.current()
        if tab and not self.worker:
            self.status.setText(f'Элементов: {tab.proxy.rowCount(tab.view().rootIndex())}   ·   Выбрано: {len(tab.selected())}' + ('   ·   Поиск только в этой папке' if tab.proxy.query else ''))
    def error(self,message):QMessageBox.warning(self,'Проводник',str(message))
    def open_path(self,path):
        if self.worker:return
        path=Path(path)
        if path.is_dir():self.navigate(path);return
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            try:self.main.load_folder(path.parent,selected=path);self.accept()
            except OSError as exc:self.error(exc)
        else:QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    def open_selected(self):
        if self.editing_text() or self.worker:return
        paths=self.current().selected()
        if len(paths)==1:self.open_path(paths[0])
        elif paths:
            media=[p for p in paths if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
            if media:self.main.open_library_selection(media,media[0],self.current().folder);self.accept()
    def create_folder(self):
        if self.worker:return
        name,ok=QInputDialog.getText(self,'Создать папку','Имя папки:',text='Новая папка')
        if ok:
            try:(self.current().folder/valid_name(name)).mkdir();self.refresh()
            except (OSError,ValueError) as exc:self.error(exc)
    def rename_selection(self):
        if self.editing_text() or self.worker:return
        paths=self.current().selected()
        if len(paths)!=1:return
        path=paths[0];name,ok=QInputDialog.getText(self,'Переименовать','Новое имя:',text=path.name)
        if ok:
            try:
                target=path.with_name(valid_name(name))
                if target==path:return
                self.main.stop_all_video();rename_no_replace(path,target)
                self.main.refresh_after_batch([dict(status='OK',kind='rename',source=path,target=target)]);self.refresh()
            except (OSError,ValueError) as exc:self.error(exc)
    def copy_selection(self,move):
        if self.editing_text() or self.worker:return
        paths=self.current().selected()
        if not paths:return
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        mime.setData('Preferred DropEffect',(2 if move else 1).to_bytes(4,'little'));QApplication.clipboard().setMimeData(mime)
        self.status.setText(f'{"Вырезано" if move else "Скопировано"}: {len(paths)}. Откройте папку назначения и нажмите «Вставить».')
    def paste_selection(self):
        if self.editing_text() or self.worker:return
        mime=QApplication.clipboard().mimeData()
        if mime is None:return
        paths=[Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
        if not paths:return
        effect=mime.data('Preferred DropEffect')
        if not effect:effect=mime.data('application/x-qt-windows-mime;value="Preferred DropEffect"')
        move=int.from_bytes(bytes(effect)[:4],'little')==2
        self.main.stop_all_video();self.worker=TransferThread(paths,self.current().folder,move,self)
        self.worker.finished.connect(self.transfer_finished);self.worker.start();self.sync()
        self.status.setText('Перемещение…' if move else 'Копирование…')
    def transfer_finished(self):
        worker=self.worker;self.worker=None
        if not worker.error and worker.move:
            mime=QApplication.clipboard().mimeData()
            if mime and [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]==worker.paths:QApplication.clipboard().clear()
        self.main.refresh_after_batch([]);self.refresh()
        if worker.error:self.error(worker.error)
        else:self.status.setText('Операция завершена. Совпадающие имена сохранены как отдельные копии.')
        worker.deleteLater()
    def trash_selection(self):
        if self.editing_text() or self.worker:return
        paths=self.current().selected()
        if not paths:return
        if QMessageBox.question(self,'Переместить в корзину',f'Переместить выбранные элементы ({len(paths)}) в корзину?',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.main.stop_all_video();errors=[]
        for path in paths:
            result=QFile.moveToTrash(str(path));success=result[0] if isinstance(result,tuple) else result
            if not success:errors.append(str(path))
        self.main.refresh_after_batch([]);self.refresh()
        if errors:self.error('Не удалось переместить в корзину:\n'+'\n'.join(errors))
    def context_menu(self,point):
        menu=QMenu(self)
        for label,callback,enabled in [('Открыть',self.open_selected,bool(self.current().selected())),
            ('Копировать',lambda:self.copy_selection(False),self.copy.isEnabled()),('Вырезать',lambda:self.copy_selection(True),self.cut.isEnabled()),
            ('Вставить',self.paste_selection,self.paste.isEnabled()),('Переименовать',self.rename_selection,self.rename.isEnabled()),
            ('В корзину',self.trash_selection,self.delete.isEnabled()),('Создать папку',self.create_folder,not self.worker)]:
            action=menu.addAction(label);action.setEnabled(enabled);action.triggered.connect(callback)
        menu.addSeparator();menu.addAction('Свойства').triggered.connect(self.properties)
        menu.addAction('Копировать путь').triggered.connect(lambda:QApplication.clipboard().setText('\n'.join(map(str,self.current().selected() or [self.current().folder]))))
        menu.exec(point)
    def properties(self):
        paths=self.current().selected() or [self.current().folder]
        lines=[]
        for path in paths[:20]:
            try:
                from datetime import datetime
                st=path.stat()
                lines.append(f'{path.name or path}\n{path}\n' + ('Папка' if path.is_dir() else f'{st.st_size:,} байт') +
                             '\nИзменён: '+datetime.fromtimestamp(st.st_mtime).strftime('%d.%m.%Y %H:%M'))
            except OSError:lines.append(str(path)+' — недоступен')
        QMessageBox.information(self,'Свойства', '\n\n'.join(lines))

    def done(self,result):
        if self.worker:self.error('Дождитесь завершения файловой операции.');return
        self.preferences.setValue('geometry',self.saveGeometry());self.preferences.sync();super().done(result)
