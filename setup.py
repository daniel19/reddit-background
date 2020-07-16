from setuptools import setup, find_packages

with open('README.md', 'r') as f:
    long_desc = ''
    for line_count, line in enumerate(f):
        if line_count == 3:
            short = line
        long_desc += line

setup(
        name='reddit-background',
        version='0.2.1',
        description=short,
        long_description=long_desc,
        long_description_content_type='text/markdown',
        packages=find_packages(),
        data_files=[('/etc/bash_completion.d/', ['background/etc/reddit_background.bash'])],
        entry_points={
            'console_scripts': ['reddit_background=background.reddit_background:main']
        },
        python_requires='>=3', 
        install_requires=['importlib-resources']
)

