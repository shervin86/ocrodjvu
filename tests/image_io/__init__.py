# encoding=UTF-8

# Copyright © 2010-2015 Jakub Wilk <jwilk@jwilk.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 dated June, 1991.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.

import os
from cStringIO import StringIO

from PIL import Image

import djvu.decode

from ocrodjvu import image_io

from tests.common import (
    assert_equal,
    assert_true,
    sorted_glob,
)

here = os.path.dirname(__file__)
try:
    here = os.path.relpath(here)
except AttributeError:
    # Python 2.5. No big deal.
    pass

formats = image_io.PNM, image_io.BMP, image_io.TIFF

def _test_from_file(base_filename, format):
    if format.bpp == 1:
        layers = djvu.decode.RENDER_MASK_ONLY
    else:
        layers = djvu.decode.RENDER_COLOR
    base_filename = os.path.join(here, base_filename)
    djvu_filename = '%s.djvu' % (base_filename,)
    expected_filename = '%s_%dbpp.%s' % (base_filename, format.bpp, format.extension)
    with open(expected_filename, 'rb') as file:
        expected = file.read()
    context = djvu.decode.Context()
    document = context.new_document(djvu.decode.FileUri(djvu_filename))
    page_job = document.pages[0].decode(wait=True)
    file = StringIO()
    format.write_image(page_job, layers, file)
    result = file.getvalue()
    assert_equal(len(result), len(expected))
    if result == expected:
        # The easy part:
        return
    else:
        # The result might be still correct, even if the strings are different.
        # Think of BMP format and its padding bytes.
        expected = Image.open(expected_filename)
        result = Image.open(StringIO(result))
        assert_equal(result.format, expected.format)
        assert_equal(result.size, expected.size)
        assert_equal(result.mode, expected.mode)
        if result.palette is None:
            assert_true(expected.palette is None)
        else:
            assert_equal(list(result.palette.getdata()), list(expected.palette.getdata()))
        assert_equal(list(result.getdata()), list(expected.getdata()))

def test_from_file():
    for djvu_filename in sorted_glob(os.path.join(here, '*.djvu')):
        base_filename = os.path.basename(djvu_filename[:-5])
        for format in formats:
            for bpp in 1, 24:
                yield _test_from_file, base_filename, format(bpp)

# vim:ts=4 sts=4 sw=4 et
