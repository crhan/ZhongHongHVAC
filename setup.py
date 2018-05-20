# -*- coding: utf-8 -*-
import re

from setuptools import setup

from zhong_hong_hvac.version import __version__

setup(
    name='zhong_hong_hvac',
    version=__version__,
    description='Python library for interfacing with ZhongHong HVAC controller',
    long_description=
    'Python library for interfacing with ZhongHong HVAC controller',
    url='https://github.com/crhan/ZhongHongHVAC',
    author='Ruohan Chen',
    author_email='crhan123@gmail.com',
    license='Apache',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3 :: Only',
    ],
    keywords='zhonghong hvac climate',
    packages=["zhong_hong_hvac"],
    python_requires='>=3.5',
    install_requires=[
        'attrs',
    ])
