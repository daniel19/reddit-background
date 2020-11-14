#!/usr/bin/env python
import json
import requests
import os

from importlib_resources import read_text


class ImgurWallpaper(object):
    __imgur_credentials = json.loads(read_text('background.resources', 'credentials.json'))

    def __init_(self):
        raise NotImplementedError

    @classmethod
    def request_from_api(cls, reddit_url, request_bucket):
        response = requests.get('{}{}{}'.format(cls.__imgur_credentials['credentials']['endpoint'], request_bucket,
                                                cls._get_imgur_id(reddit_url)),
                                headers={'Authorization': 'Client-ID {}'.format(
                                    cls.__imgur_credentials['credentials']['client_id'])})
        if response.status_code == 200:
            return response.json()
        return None

    @classmethod
    def load_imgur_album(cls, url: str) -> list:
        result = []
        json_dict = ImgurWallpaper.request_from_api(url, 'album/')
        if json_dict and json_dict['success']:
            for dicts in json_dict['data']['images']:
                image_link = dicts['link']
                dicts['thumbnail_link'] = cls._get_thumbnail_link(image_link)
                result.append(dicts)

        return result

    @classmethod
    def load_from_api(cls, url: str) -> dict:
        json_dict = ImgurWallpaper.request_from_api(url, 'image/')
        if json_dict and json_dict['success']:
            res = json_dict['data']
            res['thumbnail_link'] = cls._get_thumbnail_link(url)
            return res
        return None

    @classmethod
    def _get_thumbnail_link(cls, url):
        return '{}{}h.{}'.format(cls._get_baselink(url), cls._get_imgur_ext(url), cls._get_imgur_id(url))

    @classmethod
    def _get_imgur_ext(cls, url):
        return os.path.splitext(url)[1]

    @classmethod
    def _get_baselink(cls, url):
        return url[url.find(os.path.basename(url))]

    @classmethod
    def _get_imgur_id(cls, url) -> str:
        base = os.path.basename(url)
        if '.' in base:
            index = base.rfind('.')
            return base[:index]
        else:
            return base
        return None


    @classmethod
    def is_single_image(cls, url: str) -> bool:
        if cls.load_from_api(url):
            return True
        return False


def main():
    imgur_album = 'https://imgur.com/a/cRk1KIi'
    print(ImgurWallpaper.load_imgur_album(imgur_album))


if __name__ == '__main__':
    main()
