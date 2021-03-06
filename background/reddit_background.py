#!/usr/bin/env python
"""
NAME

    reddit-background

SYNOPSIS

    reddit-background [options] [SUBREDDITS]

DESCRIPTION

    Set Mac OS X and Linux desktop backgrounds to images pulled from Reddit.

EXAMPLES

    reddit-backgrounds CarPorn:top:10:week {seasonal} EarthPorn:new

AUTHOR

    Rick Harris <rconradharris@gmail.com>
"""

import argparse
import datetime
import fontconfig
import glob
import hashlib
import json
import math
import operator
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import urllib.parse as urlparse

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from configparser import ConfigParser, NoOptionError
from background.imgur.imgur_loader import ImgurWallpaper
from urllib.request import HTTPError
from urllib.request import URLError
from urllib.request import build_opener
from urllib.request import urlretrieve
from urllib.error import HTTPError

# since PIL is not in the standard library, using a try so it isn't necessary for the script
try:
    from PIL import Image as pilImage, ImageColor as pilImageColor, ImageDraw as pilImageDraw, ImageFont as pilImageFont
    pil_available = True
except ImportError:
    pil_available = False

__version__ = '2.1beta'

# Defaults
DEFAULT_SUBREDDIT_TOKENS = ['{seasonal}']
DEFAULT_CONFIG_PATH = u"~/.reddit-background.conf"
DEFAULT_DOWNLOAD_DIRECTORY = u"~/Reddit Backgrounds"
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; U; Linux i686) Gecko/20071127 Firefox/2.0.0.11"
DEFAULT_IMAGE_CHOOSER = 'random'
DEFAULT_IMPRINT_SIZE_TOKENS = ['auto', 50, 8, 40]
DEFAULT_IMPRINT_FONT_TOKENS = ['Arial', 50, '#CCCCCC']

# Regexs
RE_RESOLUTION_DISPLAYS = re.compile("Resolution: (\d+)\sx\s(\d+)")
RE_TITLE_TAGS = re.compile('\[[^]]*]', flags=re.DOTALL)

# Globals
_VERBOSITY = 0
_WHAT = False
_DOWNLOAD_DIRECTORY = None
_IMAGE_COUNT = 0
_OS_HANDLER = None  # Set below...
_IMAGE_CHOOSER = None
_IMAGE_SCALING = None

# Consts
WEIGHT_ASPECT_RATIO = 1.0
WEIGHT_RESOLUTION = 1.0
WEIGHT_JITTER = 0.25
WEIGHT_REDDIT_SCORE = 1.0


def set_verbosity(verbosity):
    global _VERBOSITY
    if verbosity:
        _VERBOSITY = verbosity
    else:
        _VERBOSITY = 0


def get_verbosity():
    return _VERBOSITY


def set_what(what):
    global _WHAT
    _WHAT = what


def get_what():
    return _WHAT


def set_download_directory(directory):
    global _DOWNLOAD_DIRECTORY
    _DOWNLOAD_DIRECTORY = directory


def get_download_directory():
    dirname = _DOWNLOAD_DIRECTORY or DEFAULT_DOWNLOAD_DIRECTORY
    # This needs to be Unicode so that os.listdir returns Unicode filenames
    return str(os.path.expanduser(dirname))


def set_image_count(image_count):
    global _IMAGE_COUNT
    _IMAGE_COUNT = image_count


def get_image_count():
    return _IMAGE_COUNT


def set_image_chooser(image_chooser):
    global _IMAGE_CHOOSER
    _IMAGE_CHOOSER = image_chooser


def get_image_chooser():
    return _IMAGE_CHOOSER or DEFAULT_IMAGE_CHOOSER


def set_image_scaling(image_scaling):
    global _IMAGE_SCALING
    _IMAGE_SCALING = image_scaling


def get_image_scaling():
    return _IMAGE_SCALING


def set_background_setting(setting):
    global _BG_SETTING
    _BG_SETTING = setting


def get_background_setting():
    return _BG_SETTING


def _safe_makedirs(name, mode=0o777):
    if not os.path.exists(name):
        os.makedirs(name, mode=mode)


def warn(msg):
    """Print a warning to stderr"""
    print('warning: {}'.format(msg), file=sys.stderr)


def log(msg, level=1):
    """Log to stderr

    -v is level 1
    -vv is level 2
    etc...
    """
    if get_verbosity() >= level:
        print(msg, file=sys.stderr)


class OSHandler(object):
    """Any OS specific code should go in these classes."""

    def set_background(self, path, **kwargs):
        raise NotImplementedError

    def get_desktop_resolutions(self):
        raise NotImplementedError

    @classmethod
    def get_handler(cls):
        if sys.platform == 'darwin':
            return DarwinHandler()
        elif 'linux' in sys.platform:
            return LinuxHandler()
        else:
            raise Exception("OS not supported")


class DarwinHandler(OSHandler):
    def set_background(self, path, **kwargs):
        script = u'tell application "System Events" to set picture of item' \
                 u' {num} of (a reference to every desktop) to "{path}"' \
            .format(num=kwargs['num'], path=path)
        cmd = ['/usr/bin/osascript', '-e', script]
        if subprocess.call(cmd):
            warn(u"unable to set background to '{}'".format(path))

    def get_desktop_resolutions(self):
        p = subprocess.Popen(["/usr/sbin/system_profiler", "SPDisplaysDataType"],
                             stdout=subprocess.PIPE)
        (output, err) = p.communicate()

        if err:
            log(err)

        return re.findall(RE_RESOLUTION_DISPLAYS, output)


class LinuxHandler(OSHandler):
    def set_background(self, path, **kwargs):
        bg_setting = kwargs['bg_setting']
        if bg_setting not in ['fill', 'max', 'tile', 'center', 'scale']:
            bg_setting = 'scale'

        output = subprocess.Popen(['feh --bg-{} \'{}\''.format(bg_setting, path)], shell=True,
                                  stdout=subprocess.PIPE).communicate()
        if (output[1]):
            warn(u"unable to set background to '{}'".format(path))
        else:
            stdout, stderr = subprocess.Popen(['sudo cp \'{}\' /usr/share/backgrounds/default.png'.format(path)],
                                               shell=True, stdout=subprocess.PIPE).communicate()
            if stderr:
                warn(stderr)
            log(stdout)

    def get_desktop_resolutions(self):
        # source: http://stackoverflow.com/questions/8705814/get-display-count-and-
        # resolution-for-each-display-in-python-without-xrandr
        import Xlib.ext.randr
        from Xlib import display
        d = display.Display()
        s = d.screen()
        window = s.root.create_window(0, 0, 1, 1, 1, s.root_depth)
        res = Xlib.ext.randr.get_screen_resources(window)
        modes = []
        matches = []
        for output in res.outputs:
            # use largest resolution per output
            available_modes = Xlib.ext.randr.get_output_info(window, output, 0).modes
            if len(available_modes) > 0:
                modes.append(available_modes[0])

        for mode in res.modes:
            if mode.id in modes:
                matches.append((mode.width, mode.height))
        return matches

    def _get_desktop_env(self):
        pass


class ImageChooser(object):
    def __init__(self, desktop, images):
        self.desktop = desktop
        self.images = images

    def sort(self):
        raise NotImplementedError


class RandomImageChooser(ImageChooser):
    def sort(self):
        random.shuffle(self.images)


class BestMatchImageChooser(ImageChooser):

    def _score_reddit_score(self, image, log_lo_score, log_hi_score):
        """Scoring criteria: The higher the Reddit score, the better the
        image.

        Since Reddit scores are exponential ('hot' stuff is *much* higher in
        score than 'cold' stuff), we normalize using the log of the
        reddit_score.

        0 < score <= 1
        """
        log_score = math.log1p(image.raw_reddit_score)
        if log_hi_score > log_lo_score:
            score = float(log_score - log_lo_score) / (log_hi_score - log_lo_score)
        elif log_hi_score == log_lo_score:
            # Avoid division by zero
            score = 0.0
        else:
            raise AssertionError("log_hi_score shouldn't exceed log_lo_score")
        log(u"reddit_score={:.2f} score={:.2f}".format(
            image.reddit_score, score), level=3)
        return score

    def _score_aspect_ratio(self, image):
        """Scoring criteria: The closer an image is to the aspect ratio of the
        desktop the better.

        0 < score <= 1
        1 is an exact match in terms of aspect ratio.
        """
        image_aspect_ratio = float(image.width) / image.height
        desktop_aspect_ratio = float(self.desktop.width) / self.desktop.height

        if image_aspect_ratio > desktop_aspect_ratio:
            score = desktop_aspect_ratio / image_aspect_ratio
        else:
            score = image_aspect_ratio / desktop_aspect_ratio

        log(u"image_aspect_ratio={:.2f} desktop_aspect_ratio={:.2f} score={:.2f}".format(
            image_aspect_ratio, desktop_aspect_ratio, score), level=3)

        return score

    def _score_resolution(self, image):
        """Scoring criteria: The higher the resolution an image the better.
        However, an image with resolution higher than the desktop is not
        better than any other image with resolution better than the desktop.

        0 < score <= 1
        1 best resolution
        """
        image_pixels = image.width * image.height
        desktop_pixels = self.desktop.width * self.desktop.height

        if image_pixels > desktop_pixels:
            score = 1.0
        else:
            score = float(image_pixels) / desktop_pixels

        log(u"image_pixels={:.2f} desktop_pixels={:.2f} score={:.2f}".format(
            image_pixels, desktop_pixels, score), level=3)

        return score

    def _score_jitter(self, image):
        score = random.random()
        log(u"jitter score={:.2f}".format(score), level=3)
        return score

    def sort(self):
        """
        Image Choosing Algorithm

        For images pulled from Reddit, we have the width and height metadata
        and so we can prefer images that will look better on a given desktop.

        To handle this we will assign a 'weight' to each image based on some
        criteria and then sort the images by that weight.

        The criteria is as follows:

            1) Aspect ratio: images that are similar in aspect ratio to the
               current desktop are preferred

            2) Resolution: images with higher resolution are preferred

            3) Jitter: a bit of randomness is added to that successive runs
               produce different results

            4) Reddit Score: images with higher scores on reddit are preferred

        The best images should go last because we treat this like a stack.
        """
        images = self.images
        log('Total candidate images: {}'.format(len(images)))

        raw_reddit_scores = [i.raw_reddit_score for i in images]
        log_lo_score = math.log1p(min(raw_reddit_scores))
        log_hi_score = math.log1p(max(raw_reddit_scores))

        # Score each image based on our criteria and their associated weight
        for image in images:
            log(u"Score components for '{}'".format(image.display_title), level=3)
            image.aspect_ratio_score = (
                    WEIGHT_ASPECT_RATIO * self._score_aspect_ratio(image))
            image.resolution_score = (
                    WEIGHT_RESOLUTION * self._score_resolution(image))
            image.jitter_score = (
                    WEIGHT_JITTER * self._score_jitter(image))
            image.reddit_score = (
                    WEIGHT_REDDIT_SCORE * self._score_reddit_score(
                image, log_lo_score, log_hi_score))
            score_parts = [image.aspect_ratio_score,
                           image.resolution_score,
                           image.jitter_score,
                           image.reddit_score]
            image.score = float(sum(score_parts)) / len(score_parts)

        # Sort so highest scoring images are last
        images.sort(key=operator.attrgetter('score'))

        # Display score table
        log(u"{:>10}{:>10}{:>10}{:>10}{:>10}{:>10} {}".format(
            u"Ranking",
            u"Score",
            u"Aspect",
            u"Res",
            u"Reddit",
            u"Jitter",
            u"Title"),
            level=2)
        log(u"=" * 120, level=2)
        for ranking, image in enumerate(images):
            log(u"{:>10d}{:>10.2f}{:>10.2f}{:>10.2f}{:>10.2f}{:>10.2f} {}".format(
                len(images) - ranking,
                image.score,
                image.aspect_ratio_score,
                image.resolution_score,
                image.reddit_score,
                image.jitter_score,
                image.display_title),
                level=2)


_IMAGE_CHOOSER_CLASSES = {
    'random': RandomImageChooser,
    'bestmatch': BestMatchImageChooser,
}


class ImprintConf(object):
    def __init__(self):
        self.set_position_tokens(None)
        self.set_size_tokens(None)
        self.set_font_tokens(None)

    def __repr__(self):
        return '{}, {}, {}, {}, {}'.format(self.font_size, self.box_width, self.font_filename, self.margin,
                                           self.padding)

    def _parse_token(self, tokens, default_tokens, pos, conv, errormsg):
        if tokens is None or len(tokens) <= pos:
            tokens = default_tokens
        val = tokens[pos]
        if conv is not None:
            try:
                val = conv(val)
            except:
                log(errormsg % val)
                return default_tokens[pos]
        return val

    def set_position_tokens(self, tokens):
        tokens = tokens or []
        self.position_tokens = []
        for token in tokens:
            self.position_tokens.extend(token.split() if token else [])

    def set_size_tokens(self, tokens):
        self.box_width = self._parse_token(tokens, DEFAULT_IMPRINT_SIZE_TOKENS,
                                           0, str, 'Invalid box width specified for title imprint: %s')
        self.margin = self._parse_token(tokens, DEFAULT_IMPRINT_SIZE_TOKENS,
                                        1, int, 'Invalid box margin specified for title imprint: %s')
        self.padding = self._parse_token(tokens, DEFAULT_IMPRINT_SIZE_TOKENS,
                                         2, int, 'Invalid box padding specified for title imprint: %s')
        self.transparency = self._parse_token(tokens, DEFAULT_IMPRINT_SIZE_TOKENS,
                                              3, int, 'Invalid box transparency specified for title imprint: %s')
        self.transparency = max(0, min(100, self.transparency))

    def set_font_tokens(self, tokens):
        self.font_filename = self._parse_token(tokens, DEFAULT_IMPRINT_FONT_TOKENS,
                                               0, None, None)
        self.font_size = self._parse_token(tokens, DEFAULT_IMPRINT_FONT_TOKENS,
                                           1, int, 'Invalid font specified for title imprint: %s')
        self.font_color = self._parse_token(tokens, DEFAULT_IMPRINT_FONT_TOKENS,
                                            2, None, None)

        def __str__(self):
            '''For debugging'''

        ret = []
        for name in ('position_tokens', 'box_width', 'margin',
                     'padding', 'transparency', 'font_filename', 'font_size',
                     'font_color'):
            ret.append('%s:%s' % (name, getattr(self, name)))
        return '\n'.join(ret)


class Desktop(object):
    def __init__(self, num, width, height, subreddit_tokens=None):
        self.num = num
        self.width = width
        self.height = height
        self.subreddit_tokens = subreddit_tokens or []
        self.imprint_conf = ImprintConf()
        self.bg_setting = 'fill'

        # NOTE: we need to get the download-images at instantiation time and
        # cache the results b/c we'll later clear the download directory
        # before downloading the new images, meaning we can no longer tell
        # what's already been downloaded
        # self.downloaded_images = self._get_downloaded_images()

    def __repr__(self):
        return '<Desktop {}, {}, {}, {}, {}>'.format(self.num, self.width, self.height, self.subreddits, self.imprint_conf)

    @property
    def subreddits(self):
        return [Subreddit.create_from_token(self, t)
                for t in self.subreddit_tokens]
    
    @property
    def downloaded_images(self):
        return self._get_downloaded_images()

    @property
    def download_directory(self):
        """Images are stored in ~/Reddit Backgrounds/Desktop 1"""
        subdir = 'Desktop {}'.format(self.num)
        return os.path.join(get_download_directory(), subdir)

    def _get_downloaded_images(self):
        image_hash = {}
        if not os.path.exists(self.download_directory):
            return {}
        
        for filename in os.listdir(self.download_directory):
            full_path = os.path.join(self.download_directory, filename)
            image_hash[filename] = self.__get_hash(full_path)

        return image_hash

    def __get_hash(self, full_image_path):
        with open(full_image_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()

    def _images_different(self, image):
        """
        """
        # Download to temp directory.
        path = _download_to_directory(image.url, '/tmp', image.filename)
        
        if not image.filename in self.downloaded_images.keys():
            new_path = '{}/{}'.format(self.download_directory, image.filename)
            if not os.path.exists(self.download_directory):
                os.mkdir(self.download_directory)

            shutil.move(path, new_path)
            return (new_path, False)
        
        hash_md5 = self.__get_hash(path)
        duplicate_not_found = False
        image_numbers = []

        image_title, ext = os.path.splitext(image.filename)
        for key_name in self.downloaded_images.keys():
            key_title, _ = os.path.splitext(key_name)
            if image_title in key_title:
                digest_to_comapre = self.downloaded_images[key_name]
                if not hash_md5 == digest_to_comapre:
                    log('{} does not equal {} for name {}'.format(hash_md5, digest_to_comapre, image.filename),  level=2)
                    duplicate_not_found = True
                    rgx = re.match('.*?([0-9]+)$', key_title)
                    if rgx:
                        image_numbers.append(int(rgx.group(1)))
                    else:
                        image_numbers.append(0)

        
        image_numbers.sort()
        if duplicate_not_found:
            digit = image_numbers[-1]
            new_filename = ''
            if digit == 0:
                new_filename = '{}1{}'.format(image_title, ext)
            else:
                digit += 1
                new_filename = '{}{}{}'.format(image_title, digit, ext)
            
            new_path = '{}/{}'.format(self.download_directory, new_filename) 
            shutil.move(path, new_path)
            return (new_path, True)
        
        return ('', False)


    def fetch_backgrounds(self, image_count):
        random_subreddit = random.choice(self.subreddits)
        images = random_subreddit.fetch_images() 
        chooser_cls = _IMAGE_CHOOSER_CLASSES[get_image_chooser()]
        chooser = chooser_cls(self, images)
        chooser.sort()

        log(u'Number of images to download: {0}'.format(image_count))
        result_images = []
        count = 0

        for image in images:
            if count >= image_count:
                break
            try:
                # Don't re-use an image that's already downloaded
                path, result  = self._images_different(image)
                image.file_path = path
                if not result:
                    log(u"'{}' already downloaded, skipping...".format(
                        image.filename), level=2)
            except URLOpenError:
                warn(u"unable to download '{}', skipping...".format(image.url))
                continue  # Try next image...
            else:
                result_images.append(image)
                if get_image_scaling() == 'fit':
                    image.fit_to_desktop(self)
                if self.imprint_conf.position_tokens:
                    image.imprint_title(self)
            count += 1
        return result_images 

    def set_background(self, image):
        log(u'Setting background for desktop {0}'.format(self.num))
        _OS_HANDLER.set_background(image.file_path, num=self.num, bg_setting=self.bg_setting)


def _get_desktops_with_defaults():
    """Desktop objects populated with sensible defaults.

    Customizations will be performed by overriding these values, first using
    the config file and later command-line options.
    """
    desktops = []
    for num, res in enumerate(_OS_HANDLER.get_desktop_resolutions(), start=1):
        width = int(res[0])
        height = int(res[1])
        desktop = Desktop(num, width, height,
                          subreddit_tokens=DEFAULT_SUBREDDIT_TOKENS)
        desktops.append(desktop)
    return desktops


class URLOpenError(Exception):
    pass


def _urlopen(url):
    opener = build_opener()
    opener.addheaders = [('User-Agent', DEFAULT_USER_AGENT)]
    try:
        return opener.open(url)
    except (socket.error,
            HTTPError,
            URLError):
        raise URLOpenError


def _download_to_directory(url, dirname, filename):
    """Download a file to a particular directory"""
    _safe_makedirs(dirname)

    path = os.path.join(dirname, filename)

    log(u"Downloading '{0}' to '{1}'".format(url, path))
    try:
        urlretrieve(url, path)
    except HTTPError as e:
        log(e)

    op=open(path)
    op.close()
    return path


def _clear_download_directory(desktops):
    dirname = get_download_directory()
    # check contents of download dir
    if os.path.exists(dirname):
        folders = os.listdir(dirname)
        if len(folders) == len(desktops):
            for f in folders:
                files = glob.glob('{}/{}/*'.format(dirname, f))
                for fs in files:
                    os.remove(fs)
        else:
            shutil.rmtree(dirname)
            _safe_makedirs(dirname)


def _get_northern_hemisphere_season():
    """Source: http://stackoverflow.com/questions/16139306/determine-season-given-timestamp-in-python-using-datetime"""
    day = datetime.date.today().timetuple().tm_yday
    spring = range(80, 172)
    summer = range(172, 264)
    autumn = range(264, 355)
    if day in spring:
        return 'spring'
    elif day in summer:
        return 'summer'
    elif day in autumn:
        return 'autumn'
    else:
        return 'winter'


def slugify(value):
    """
    Normalizes string, converts to lowercase, removes non-alpha characters,
    and converts spaces to hyphens.
    """
    import unicodedata
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore')
    if type(value) == bytes:
        value = value.decode('utf-8')

    value = str(re.sub('[^\w\s-]', '', value).strip().lower())
    value = str(re.sub('[-\s]+', '-', value))
    return value


class Image(object):
    TITLE_MAX_LENGTH = 64

    def __init__(self, width, height, url, thumbnail_url, title, raw_reddit_score,
                 score=0.0,
                 aspect_ratio_score=0.0,
                 resolution_score=0.0,
                 jitter_score=0.0,
                 reddit_score=0.0,
                 image_id=None):
        self.width = width
        self.height = height
        self._url = url
        self._thumbnail_url = thumbnail_url
        self.title = title
        self.raw_reddit_score = raw_reddit_score
        self.score = score
        self.aspect_ratio_score = aspect_ratio_score
        self.resolution_score = resolution_score
        self.jitter_score = jitter_score
        self.reddit_score = reddit_score
        self.image_id = image_id
        self.file_path = None

    @property
    def url(self):
        return self._url.replace('amp;', '')

    @property
    def thumbnail_url(self):
        return self._thumbnail_url.replace('amp;', '')

    @property
    def display_title(self):
        if len(self.title) <= self.TITLE_MAX_LENGTH:
            return self.title
        else:
            return self.title[:self.TITLE_MAX_LENGTH - 3] + u'...'

    @property
    def full_title(self):
        return RE_TITLE_TAGS.sub('', self.title)

    @property
    def filename(self):
        filename = slugify(self.display_title)

        # Get extension from URL
        url_path = urlparse.urlparse(self.url).path
        parts = url_path.rsplit('.', 1)
        try:
            filename += u'.' + parts[1]
        except IndexError:
            pass
        return filename

    def _ensure_pil_available(self, option):
        if not pil_available:
            raise ImportError(u"Cannot imprint title on image because the"
                              u" python imaging library is not available."
                              u" Please install `pillow` or remove the '%s'"
                              u" option from your config." % option)

    def fit_to_desktop(self, desktop):
        self._ensure_pil_available('fit')
        if self.file_path:
            img = pilImage.open(self.file_path)
        else:
            img = pilImage.open(os.path.join(desktop.download_directory, self.filename))


        image_ratio = float(img.width) / float(img.height)
        desktop_ratio = float(desktop.width) / float(desktop.height)
        if image_ratio == desktop_ratio:
            # If exact match, then no resize necessary...
            return
        elif image_ratio < desktop_ratio:
            img = img.resize((int(desktop.height * image_ratio), desktop.height))
        else:
            img = img.resize((desktop.width, int(desktop.width / image_ratio)))
        canvas = pilImage.new('RGB', (desktop.width, desktop.height), (0, 0, 0))
        canvas.paste(img, (max(0, int((desktop.width - img.width) / 2.0)),
                           max(0, int((desktop.height - img.height) / 2.0))))
        img = canvas
        if self.file_path:
            img.save(self.file_path, "JPEG", quality=85)
        else:
            img.save(os.path.join(desktop.download_directory, self.filename),
                 "JPEG", quality=85)
        self.width = img.width
        self.height = img.height

    def _wrap_text(self, draw, text, maxwidth, font):
        """Split text into lines that are less than maxwidth for a given PIL
        font
        """
        lines = []
        for tl in text.splitlines():
            curparts = []
            for part in (t for t in tl.split() if t):
                curparts.append(part)
                if draw.textsize(u' '.join(curparts), font=font)[0] > maxwidth:
                    if len(curparts) == 1:
                        lines.append(u' '.join(curparts))
                        curparts = []
                    else:
                        lines.append(u' '.join(curparts[:-1]))
                        curparts = [curparts[-1]]
            if len(curparts) > 0:
                lines.append(u' '.join(curparts))
        return lines

    def _get_imprint_font(self, desktop, default='/usr/share/wine/fonts/arial.ttf'):
        conf = desktop.imprint_conf
        fonts = fontconfig.query(lang='en')
        try:
            for f in fonts:
                if conf.font_filename in f:
                    break 

            font = pilImageFont.truetype(f, conf.font_size)
        except IOError:
            warn("Cannot open font file {}.".format(conf.font_filename))
            font = pilImageFont.truetype(default, conf.font_size)
        return font

    def imprint_title(self, desktop):
        self._ensure_pil_available('imprint_position')

        if not self.full_title:
            return

        img = pilImage.open(os.path.join(desktop.download_directory, self.filename))

        draw = pilImageDraw.ImageDraw(img)

        conf = desktop.imprint_conf

        if conf.box_width.strip().lower() == 'auto':
            maxwidth = desktop.width
        else:
            maxwidth = int(conf.box_width)

        font = self._get_imprint_font(desktop)

        lines = self._wrap_text(draw, self.full_title, maxwidth, font)

        # Compute box height and width
        maxwidth = max(draw.textsize(l, font=font)[0] for l in lines)

        lineheight = draw.textsize(self.title, font=font)[1]
        maxheight = len(lines) * lineheight

        # Calculate the x and y
        if 'left' in conf.position_tokens:
            x = conf.margin
        elif 'right' in conf.position_tokens:
            x = max(conf.margin, img.width - maxwidth - conf.margin)
        else:
            x = max(conf.margin, (img.width - maxwidth) / 2)
        if 'top' in conf.position_tokens:
            y = conf.margin
        elif 'bottom' in conf.position_tokens:
            y = max(conf.margin, img.height - maxheight - conf.margin)
        else:
            y = max(conf.margin, (img.height - maxheight) / 2)

        # Draw the background box with transparent color, then composite with
        # the original image
        canvas = pilImage.new('RGBA', img.size)
        draw = pilImageDraw.ImageDraw(canvas)

        box_x0 = x - conf.padding
        box_y0 = y - conf.padding
        box_x1 = x + maxwidth + conf.padding
        box_y1 = y + maxheight + conf.padding
        box_fill = (0, 0, 0, int(255 * conf.transparency / 100.0))
        draw.rectangle((box_x0, box_y0, box_x1, box_y1), fill=box_fill)
        img = pilImage.alpha_composite(img.convert('RGBA'), canvas)

        # Draw the text
        draw = pilImageDraw.ImageDraw(img)
        text_x = x
        text_y = y
        text_fill = pilImageColor.getrgb(conf.font_color)
        if len(text_fill) != 3:
            text_fill = (255, 229, 204)
        for line in lines:
            draw.text((text_x, text_y), line, font=font, fill=text_fill)
            text_y += lineheight

        # Save the image
        img = img.convert('RGB')
        img.save(os.path.join(desktop.download_directory, self.filename),
                 "JPEG", quality=85)


class Subreddit(object):
    def __init__(self, desktop, name, sort='top', limit=100, timeframe='month'):
        self.desktop = desktop
        self.name = name
        self.sort = sort
        self.limit = limit
        self.timeframe = timeframe

    def fetch_images(self):
        url = 'http://reddit.com/r/{subreddit}/{sort}.json?t={timeframe}&limit={limit}'
        url = url.format(subreddit=self.name,
                         sort=self.sort,
                         timeframe=self.timeframe,
                         limit=self.limit)

        try:
            log(url)
            response = _urlopen(url)
        except URLOpenError:
            warn("error fetching images from subreddit '{0}',"
                 " skipping...".format(self.name))
            return []

        try:
            data = json.loads(response.read())
        finally:
            response.close()

        images = []
        
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(self._collect_urls, child['data']): child for child in data['data']['children']}
            for future in as_completed(futures):
                try:
                    data = future.result()
                    images = [*images, *data]
                except Exception as e:
                    log('Failed to load data {}'.format(e))
        
        log('Count of images: {}'.format(len(images)))
        return images
    
    def _collect_urls(self, data):
        images = []
        try:
            if 'imgur' in data['url']:
                imgur_url = data['url']
                if ImgurWallpaper.is_single_image(imgur_url):
                    image_data = ImgurWallpaper.load_from_api(imgur_url)
                    if image_data:
                        image_data['url'] = image_data['link']
                        image_data['score'] = image_data['views']

                        image = Image(image_data['width'],
                                image_data['height'],
                                image_data['url'],
                                image_data['thumbnail_link'],
                                data['title'],
                                int(data['score']),
                                image_id=image_data['id'])
                        log('Single Image: {}'.format(image.full_title))
                        images.append(image)
                    else:
                        log('URL returns null data : {}'.format(imgur_url))
                else:
                    # Imgur Album is found.
                    for image_data in ImgurWallpaper.load_imgur_album(imgur_url):
                        if image_data:
                            image_data['url'] = image_data['link']
                            image_data['score'] = image_data['views']
                            image = Image(image_data['width'],
                                    image_data['height'],
                                    image_data['url'],
                                    image_data['thumbnail_link'],
                                    data['title'],
                                    int(data['score']),
                                image_id=image_data['id'])
                            log('Album Image: {}'.format(image.full_title))
                            images.append(image)
                        else:
                            log('URL returns null data : {}'.format(imgur_url.full_title))
            else:
                image_data = data['preview']['images'][0]['source']
                image = Image(image_data['width'],
                        image_data['height'],
                        image_data['url'],
                        data['thumbnail'],
                        data['title'],
                        int(data['score']))
                log('Reddit Image: {}'.format(image.full_title))
                images.append(image)
        except Exception as e:
            log('Error fetching images: {}'.format(e))
        
        return images

    @classmethod
    def handle_dynamic_subreddit_seasonal(cls, token_parts):
        """Dynamic subreddit handlers mutate token_parts in order to trigger
        dynamic behavior.
        """
        season = _get_northern_hemisphere_season().capitalize()
        token_parts[0] = '{}Porn'.format(season)

    @classmethod
    def create_from_token(cls, desktop, token):
        token_parts = token.split(':')

        # Handle any dynamic subreddits
        name = token_parts[0]
        if name.startswith('{') and name.endswith('}'):
            stripped_name = name[1:-1]
            handler_name = 'handle_dynamic_subreddit_{}'.format(stripped_name)
            func = getattr(cls, handler_name)
            func(token_parts)

        args = ('name', 'sort', 'limit', 'timeframe')
        ddict = {}
        for arg, value in zip(args, token_parts):
            ddict[arg] = value
        return cls(desktop, **ddict)

    def __repr__(self):
        return '<Subreddit r/{0}>'.format(self.name)


def _read_config_file(desktops):
    path = os.path.expanduser(DEFAULT_CONFIG_PATH)

    if not os.path.exists(path):
        return

    def parse_subreddit_tokens(desktop, section):
        try:
            tokens = map(lambda x: x.strip(),
                         config.get(section, 'subreddits').split(','))
        except NoOptionError:
            pass
        else:
            if tokens:
                desktop.subreddit_tokens = tokens

    def parse_imprint_tokens(desktop, section):
        for confname, funcname in (
                ('imprint_position', 'set_position_tokens'),
                ('imprint_size', 'set_size_tokens'),
                ('imprint_font', 'set_font_tokens'),
        ):
            try:
                tokens = map(lambda x: x.strip(),
                             config.get(section, confname).split(':'))
            except NoOptionError:
                pass
            else:
                if tokens:
                    getattr(desktop.imprint_conf, funcname)(tokens)

    config = ConfigParser()
    with open(path) as f:
        config.read_file(f)

    for desktop in desktops:
        section = 'desktop{0}'.format(desktop.num)
        if section not in config.sections():
            section = 'default'
        parse_subreddit_tokens(desktop, section)
        parse_imprint_tokens(desktop, section)

    if 'default' in config.sections():
        try:
            set_image_count(config.getint('default', 'image_count'))
        except NoOptionError:
            pass
        try:
            download_directory = config.get('default', 'download_directory')
        except NoOptionError:
            pass
        else:
            if download_directory:
                set_download_directory(download_directory)
        try:
            image_chooser = config.get('default', 'image_chooser')
        except NoOptionError:
            pass
        else:
            if image_chooser:
                set_image_chooser(image_chooser)
        try:
            set_image_scaling(config.get('default', 'image_scaling'))
        except NoOptionError:
            pass
        try:
            set_background_setting(config.get('default', 'background_setting'))
        except NoOptionError:
            pass
        else:
            desktop.bg_setting = get_background_setting()



def _handle_cli_options(desktops):
    parser = argparse.ArgumentParser(
        description='set desktop background image from reddit')
    parser.add_argument('subreddits', metavar='SUBREDDITS', nargs='*',
                        help='a list of subreddits')
    parser.add_argument('--desktop', type=int, default=0,
                        help='set background for this desktop'
                             ' (default: sets background for all desktops)')
    parser.add_argument('-v', '--verbose', action='count',
                        help='log to stderr (use -vv for even more info)')
    parser.add_argument('--image-count', type=int,
                        help="number of images to download (this only downloads the"
                             " images, it doesn't set the background)")
    parser.add_argument('--download-directory',
                        help='directory to use to store images')
    parser.add_argument('--what',
                        action='store_true',
                        help='display what images are downloaded for each desktop')
    parser.add_argument('--imprint-position',
                        help='imprint the title in images at this position. horizontal'
                             ' vertical (ex: bottom:left)')
    parser.add_argument('--imprint-size',
                        help='box options for title imprinting.'
                             ' width:margin:padding:transparency')
    parser.add_argument('--imprint-font',
                        help='font options for title imprinting.'
                             ' filename:size:color')
    parser.add_argument('--version',
                        action='version',
                        version=__version__)
    parser.add_argument('--background-setting',
                        help='Set the desktop background setting.\nSee feh man page for options.')

    args = parser.parse_args()

    set_verbosity(args.verbose)
    set_what(args.what)

    if args.image_count is not None:
        set_image_count(args.image_count)

    if args.download_directory:
        set_download_directory(args.download_directory)

    if args.background_setting:
        set_background_setting(args.background_setting)

    if args.desktop:
        desktops = [d for d in desktops if d.num == args.desktop]

    for desktop in desktops:
        if args.subreddits:
            desktop.subreddit_tokens = args.subreddits
        if args.imprint_position:
            desktop.imprint_conf.set_position_tokens(args.imprint_position.split(':'))
        if args.imprint_size:
            desktop.imprint_conf.set_size_tokens(args.imprint_size.split(':'))
        if args.imprint_font:
            desktop.imprint_conf.set_font_tokens(args.imprint_font.split(':'))
        desktop.bg_setting = get_background_setting()

    return desktops


def show_whats_downloaded(desktops):
    for desktop in desktops:
        print("Desktop {}".format(desktop.num))
        for filename in desktop.downloaded_images.keys():
            print("\t{}".format(filename))


def get_desktop_config():
    global _OS_HANDLER
    _OS_HANDLER = OSHandler.get_handler()

    # Configuration override order: defaults -> config-file -> cli-options
    desktops = _get_desktops_with_defaults()
    _read_config_file(desktops)
    desktops = _handle_cli_options(desktops)
    return desktops

def main():
    desktops = get_desktop_config()

    if get_what():
        show_whats_downloaded(desktops)
        return

    image_count = get_image_count()

    _clear_download_directory(desktops)

    for desktop in desktops:
        if image_count > 0:
            # Download-only mode (downloads multiple images, but doesn't set
            # the background because we'll let the OS's native
            # background-setting utility handle it)
            desktop.fetch_backgrounds(image_count)
            log(u"Skipping setting background")
        else:
            # Set-background mode (download the best image, and set the
            # background ourselves)
            images = desktop.fetch_backgrounds(1)
            if images:
                desktop.set_background(images[0])


if __name__ == "__main__":
    main()
