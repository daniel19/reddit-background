from gi import require_version
require_version("Gtk", "3.0")

import cairo
import io
import numpy
import os
import random
import requests
import shutil  

from background.reddit_background import Image
from background.reddit_background import get_desktop_config
from background.reddit_background import _download_to_directory, _safe_makedirs

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed

from gi.repository import Gtk, Gio, GLib, GObject
from gi.repository import GdkPixbuf

from  PIL import Image as pilImage

from i3_pywal.main import wal

class SubredditModel():
    """
    """
    def __init__(self):
        desktops = get_desktop_config()
        self.subreddits = []
        self.folder_path = '/tmp/reddit_gui'
        self.subreddit_title = None
        for desktop in desktops:
            self.subreddits.extend(desktop.subreddits)


    def get_images(self):
        self._clear_folder_contents()
        _safe_makedirs(self.folder_path)
        try:
            random_subreddit = random.choice(self.subreddits)
            self.subreddit_title = random_subreddit.name            
            return random_subreddit.fetch_images()
        except IndexError as e:
            print(e)
                    
        return None

    def load_image(self, image: Image):
        path = _download_to_directory(image.url, self.folder_path, image.filename)
        return path

    def _clear_folder_contents(self):
        if os.path.isdir(self.folder_path):
            shutil.rmtree(self.folder_path)
        

class SubredditController():
    """
    """
    def __init__(self):
        self.subreddit_model = SubredditModel()
        self.view = ImageWindow(self.subreddit_model)


    def run(self):
        self.view.show_all()
        self.view.connect('destroy', Gtk.main_quit)
        Gtk.main()


class RedditImageView(Gtk.EventBox):
    """
    """
    def  __init__(self,  model, image : Image, callback=None):
        Gtk.Box.__init__(self)
        self.model = model
        self.image = image 
        self.callback = callback
        self.image_view = Gtk.Image()

        self.add(self.image_view)

        self.connect('button_press_event', self.on_pressed)
        self.load_image(self.image.url)

    def _set_image_data(self, gdaemonfile, result):
        try:
            _, data, _ = self.stream.load_contents_finish(result)

            pil: pilImage = pilImage.open(io.BytesIO(data))
            c_format = cairo.FORMAT_ARGB32
            
            if pil.mode == 'RGB':
                r, g, b = pil.split()
                pil = pilImage.merge("RGB", (b, g, r))
                pil.putalpha(256)
            elif pil.mode == 'RGBA':
                r, g, b, a = pil.split()
                pil = pilImage.merge('RGBA', (b, g, r, a))
            
            size_val = 650
            size = (size_val, size_val)
            pil = pil.resize(size)
            
            arr = numpy.array(pil)
            cai_height, cai_width, _ = arr.shape

            surface = cairo.ImageSurface.create_for_data(arr, c_format, pil.height, pil.width)
            GLib.idle_add(self.image_view.set_from_surface, surface)
        except Exception as e:
            print(e)

    def load_image(self, url):
        self.stream = Gio.file_new_for_uri(url)
        self.stream.load_contents_async(None, self._set_image_data)

    def on_pressed(self, widget, data):
        path = self.model.load_image(self.image)
        wal(image_path=path, manual=True)
        # TODO:Add some way to pause gui until wal is finished.


class ImageWindow(Gtk.Window):
    """
    """
    def __init__(self, model, title=r'Subreddit Images'):

        Gtk.Window.__init__(self, title=title)
        self.set_border_width(10)
        self.model = model

        # Create Grid View
        grid = Gtk.Grid()
        grid.set_property('orientation', Gtk.Orientation.VERTICAL) 
        # Create Sroll Window 
        scroll_view = Gtk.ScrolledWindow()
        scroll_view.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC) 
        scroll_view.set_hexpand(True)
        scroll_view.set_vexpand(True)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.BASELINE)
        self.flowbox.set_max_children_per_line(30)
        self.flowbox.set_column_spacing(0)
        self.flowbox.set_row_spacing(0)

        refresh_button = Gtk.Button.new_with_label('REFRESH')
        refresh_button.connect('clicked', self.on_click_refresh)

        scroll_view.add(self.flowbox)

        grid.add(scroll_view)
        grid.add(refresh_button)

        self.add(grid)

    def on_click_refresh(self, button):
        for child in self.flowbox.get_children():
            Gtk.Widget.destroy(child)

        for image in self.model.get_images(): 
            reddit_imageview = RedditImageView(self.model, image)
            self.flowbox.add(reddit_imageview)

        self.set_title('{} - {}'.format('Subreddit Images', self.model.subreddit_title))
        self.flowbox.show_all()
        


        

def main():
    SubredditController().run()


if __name__ == '__main__':
    main()
