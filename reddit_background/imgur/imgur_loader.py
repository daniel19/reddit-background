#!/usr/bin/env python
import json 
import requests
import re
import sys

from importlib_resources import read_text

class ImgurWallpaper(object):
    __imgur_credentials = json.loads(read_text('reddit_background.resources', 'credentials.json'))
    def __init_(self):
        raise NotImplementedError

    @classmethod
    def request_from_api(cls, reddit_url, request_bucket):
        print(cls.__imgur_credentials)
        response = requests.get('{}{}{}'.format(cls.__imgur_credentials['credentials']['endpoint'], request_bucket, cls._getImgurID(reddit_url)),
                                headers={'Authorization': 'Client-ID {}'.format(cls.__imgur_credentials['credentials']['client_id'])})
        return response.json()

    @classmethod
    def load_imgur_album(cls, url):
        result = []
        json_dict = ImgurWallpaper.request_from_api(url, 'album/')
        if json_dict['success']:
            for dicts in json_dict['data']['images']:
                result.append(dicts['link'])
        
        return result


    @classmethod
    def load_from_api(cls, url: str) -> str :
        json_dict = ImgurWallpaper.request_from_api(url, 'image/')
        if json_dict['success']:
            return json_dict['data']['link']
        return None 

    
    @classmethod
    def _getImgurID(cls, url) -> str:
        regex_compile = re.compile('[^/]+(?=/$|$)')
        matches = regex_compile.findall(url)
        if matches:
            return matches[0]
        else:
            return None

def main():
    imgur_album = 'https://imgur.com/a/cRk1KIi'
    print(ImgurWallpaper.load_imgur_album(imgur_album))

if __name__ == '__main__':
    main()
