from setuptools import setup, find_packages

with open('README.md', 'r') as f:
    long_desc = f.read()

setup(
        name='reddit-background',
        long_description=long_desc,
        long_description_content_type='text/markdown',
        packages=find_packages(),
        entry_points={
            'console_scripts': ['imgur_loader=reddit_background.imgur.imgur_loader:main']
        },
        python_requires='>=3.7',
)

