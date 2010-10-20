# encoding=UTF-8
# Copyright © 2008, 2009, 2010 Jakub Wilk <jwilk@jwilk.net>
#
# This package is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 dated June, 1991.
#
# This package is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.

from __future__ import with_statement

import argparse
import contextlib
import inspect
import locale
import os.path
import shutil
import sys
import threading
import traceback

from .. import engines
from .. import errors
from .. import ipc
from .. import temporary
from .. import text_zones
from .. import utils
from .. import version

# Import this after local modules, so that they can take care of a showing
# a nice ImportError message.
import djvu.decode

__version__ = version.__version__

system_encoding = locale.getpreferredencoding()

class Saver(object):

    in_place = False

    @utils.not_overridden
    def save(self, document, pages, djvu_path, sed_file):
        raise NotImplementedError('Cannot save results in this format')

class BundledSaver(Saver):

    '''save results as a bundled multi-page document'''

    options = '-o', '--save-bundled'

    def __init__(self, save_path):
        self._save_path = os.path.abspath(save_path)

    def save(self, document, pages, djvu_path, sed_file):
        file = open(self._save_path, 'wb')
        try:
            document.save(file=file, pages=pages)
        finally:
            file.close()
        InPlaceSaver().save(None, pages, self._save_path, sed_file)

class IndirectSaver(Saver):

    '''save results as an indirect multi-page document'''

    options = '-i', '--save-indirect'

    def __init__(self, save_path):
        self._save_path = os.path.abspath(save_path)

    def save(self, document, pages, djvu_path, sed_file):
        document.save(indirect=self._save_path, pages=pages)
        InPlaceSaver().save(None, pages, self._save_path, sed_file)

class ScriptSaver(Saver):

    '''save a djvused script with results'''

    options = '--save-script',

    def __init__(self, save_path):
        self._save_path = os.path.abspath(save_path)

    def save(self, document, pages, djvu_path, sed_file):
        shutil.copyfile(sed_file.name, self._save_path)

class InPlaceSaver(Saver):

    '''save results in-place'''

    options = '--in-place',
    in_place = True

    def save(self, document, pages, djvu_path, sed_file):
        sed_file_name = os.path.abspath(sed_file.name)
        djvu_path = os.path.abspath(djvu_path)
        djvused = ipc.Subprocess(
            ['djvused', '-s', '-f', sed_file_name, djvu_path],
        )
        djvused.wait()

class DryRunSaver(Saver):

    '''don't change any files'''

    options = '--dry-run',

    def save(self, document, pages, djvu_path, sed_file):
        pass

def get_cpu_count():
    try:
        import multiprocessing
        return multiprocessing.cpu_count()
    except (ImportError, NotImplementedError):
        pass
    try:
        return os.sysconf('SC_NPROCESSORS_ONLN')
    except (ValueError, OSError, AttributeError):
        return 1

class ArgumentParser(argparse.ArgumentParser):

    savers = BundledSaver, IndirectSaver, ScriptSaver, InPlaceSaver, DryRunSaver
    engines = list(engines.get_engines())
    [default_engine] = [engine for engine in engines if engine.name == 'ocropus']

    _details_map = dict(
        lines=text_zones.TEXT_DETAILS_LINE,
        words=text_zones.TEXT_DETAILS_WORD,
        chars=text_zones.TEXT_DETAILS_CHARACTER,
    )

    _render_map = dict(
        mask=djvu.decode.RENDER_MASK_ONLY,
        foreground=djvu.decode.RENDER_FOREGROUND,
        all=djvu.decode.RENDER_COLOR,
    )

    def __init__(self):
        usage = '%(prog)s [options] FILE'
        version = '%(prog)s ' + __version__
        argparse.ArgumentParser.__init__(self, usage=usage)
        self.add_argument('-v', '--version', action='version', version=version, help='show version information and exit')
        saver_group = self.add_argument_group(title='options controlling output')
        for saver_type in self.savers:
            options = saver_type.options
            try:
                init_args, _, _, _ = inspect.getargspec(saver_type.__init__)
                n_args = len(init_args) - 1
            except TypeError:
                n_args = 0
            metavar = None
            if n_args == 1:
                metavar = 'FILE'
            saver_group.add_argument(
                *options,
                **dict(
                    metavar=metavar,
                    action=self.set_output,
                    saver_type=saver_type, nargs=n_args,
                    help=saver_type.__doc__
                )
            )
        self.add_argument('-e', '--engine', dest='engine', nargs=1, action=self.set_engine, default=self.default_engine, help='OCR engine to use')
        self.add_argument('--list-engines', action=self.list_engines, nargs=0, help='print list of available OCR engines')
        self.add_argument('--ocr-only', dest='ocr_only', action='store_true', default=False, help='''don't save pages without OCR''')
        self.add_argument('--clear-text', dest='clear_text', action='store_true', default=False, help='remove existing hidden text')
        self.add_argument('-l', '--language', dest='language', help='set recognition language')
        self.add_argument('--list-languages', action=self.list_languages, nargs=0, help='print list of available languages')
        self.add_argument('--render', dest='render_layers', choices=self._render_map.keys(), action='store', default='mask', help='image layers to render')
        self.add_argument('-p', '--pages', dest='pages', action='store', default=None, help='pages to process')
        self.add_argument('-D', '--debug', dest='debug', action='store_true', default=False, help='''don't delete intermediate files''')
        self.add_argument('--on-error', choices=('abort', 'resume'), default='abort', help='error handling strategy' and argparse.SUPPRESS)
        # --on-error=resume should not be used by anobody. No, really.
        self.add_argument('-j', '--jobs', dest='n_jobs', metavar='N', nargs='?', type=int, default=1, help='number of jobs to run simultaneously')
        self.add_argument('path', metavar='FILE', help='DjVu file to process')
        group = self.add_argument_group(title='text segmentation options')
        group.add_argument('-t', '--details', dest='details', choices=('lines', 'words', 'chars'), action='store', default='words', help='amount of text details to extract')
        group.add_argument('--word-segmentation', dest='word_segmentation', choices=('simple', 'uax29'), default='space', help='word segmentation algorithm')

    class set_engine(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            [value] = values
            for engine in parser.engines:
                if engine.name != value:
                    continue
                namespace.engine = engine
                break
            else:
                parser.error('Invalid OCR engine name')

    class list_engines(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            for engine in parser.engines:
                try:
                    engine = engine()
                except errors.EngineNotFound:
                    pass
                else:
                    print engine.name
            sys.exit(0)

    class list_languages(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            try:
                for language in namespace.engine().list_languages():
                    print language
            except errors.EngineNotFound, ex:
                print >>sys.stderr, ex
                sys.exit(1)
            except errors.UnknownLanguageList, ex:
                print >>sys.stderr, ex
                sys.exit(1)
            else:
                sys.exit(0)

    class set_output(argparse.Action):
        def __init__(self, **kwargs):
            self.saver_type = kwargs.pop('saver_type')
            argparse.Action.__init__(self, **kwargs)
        def __call__(self, parser, namespace, values, option_string=None):
            try:
                namespace.saver
            except AttributeError:
                namespace.saver = self.saver_type(*values)
            else:
                namespace.saver = None

    def parse_args(self):
        options = argparse.ArgumentParser.parse_args(self)
        try:
            options.pages = utils.parse_page_numbers(options.pages)
        except (TypeError, ValueError):
            self.error('Unable to parse page numbers')
        options.details = self._details_map[options.details]
        options.render_layers = self._render_map[options.render_layers]
        options.resume_on_error = options.on_error == 'resume'
        try:
            saver = options.saver
        except AttributeError:
            saver = None
        if saver is None:
            self.error(
                'You must use exactly one of the following options: %s' %
                ', '.join('/'.join(saver.options) for saver in self.savers)
            )
        # It might be temping to verify language name correctness at argument
        # parse time (rather than after argument parsing). However, it's
        # desirable to be able to specify a language *before* specifying an OCR
        # engine.
        if options.language is None:
            options.language = options.engine.get_default_language()
        try:
            if not options.engine.has_language(options.language):
                self.error('Language pack for the selected language (%s) is not available.' % options.language)
        except errors.InvalidLanguageId, ex:
            self.error(str(ex))
        except errors.UnknownLanguageList:
            # For now, let's assume the language pack is installed
            pass
        options.uax29 = options.language if options.word_segmentation == 'uax29' else None
        if options.n_jobs is None:
            options.n_jobs = get_cpu_count()
        return options

class Context(djvu.decode.Context):

    def init(self, options):
        self._temp_dir = temporary.raw.mkdtemp(prefix='ocrodjvu.')
        self._debug = options.debug
        self._options = options
        bpp = 24 if self._options.render_layers != djvu.decode.RENDER_MASK_ONLY else 1
        self._image_format = self._options.engine.image_format(bpp)

    def _temp_file(self, name, auto_remove=True):
        path = os.path.join(self._temp_dir, name)
        file = open(path, 'w+b')
        if not self._debug and auto_remove:
            file = temporary.wrapper(file, file.name)
        return file

    def handle_message(self, message):
        if isinstance(message, djvu.decode.ErrorMessage):
            print >>sys.stderr, message
            sys.exit(1)

    @contextlib.contextmanager
    def get_output_image(self, nth, page_job):
        size = page_job.size
        rect = (0, 0) + size
        output_format = self._image_format
        file = self._temp_file('%06d.%s' % (nth, output_format.extension))
        try:
            output_format.write_image(page_job, self._options.render_layers, file)
            file.flush()
            yield file
        finally:
            file.close()

    def process_page(self, page):
        print >>sys.stderr, '- Page #%d' % (page.n + 1)
        page_job = page.decode(wait=True)
        size = page_job.size
        with self.get_output_image(page.n, page_job) as pfile:
            result_file = None
            with self._engine.recognize(pfile, language=self._options.language, details=self._options.details) as result:
                try:
                    if self._debug:
                        result_file = self._temp_file('%06d.%s' % (page.n, self._engine.output_format))
                        result_data = result.read()
                        result_file.write(result_data)
                        result_file.seek(0)
                    else:
                        result_file = result
                    [text] = self._engine.extract_text(result_file,
                        rotation=page.rotation,
                        details=self._options.details,
                        uax29=self._options.uax29,
                        page_size=size
                    )
                    # It should be: (page 0 0 <width> <height> …):
                    assert len(text) > 5
                    return text
                finally:
                    if result_file is not None:
                        result_file.close()

    def page_thread(self, pages, results, condition):
        for page in pages:
            n = page.n
            with condition:
                result = results[n]
                if result is not None:
                    # The page is being processed or has been already processed.
                    continue
                # Mark the page as taken.
                results[n] = True
            try:
                result = self.process_page(page)
            except djvu.decode.NotAvailable:
                print >>sys.stderr, 'No image suitable for OCR.'
                result = False
            except (SystemExit, KeyboardInterrupt), ex:
                with condition:
                    condition.notify()
                raise
            except Exception, ex:
                try:
                    message = 'Exception while processing page %d:\n%s' % (n + 1, traceback.format_exc())
                    print >>sys.stderr, message.rstrip()
                    if self._options.resume_on_error:
                        # As requested by user, don't abort on error and pretend that nothing happened.
                        results[n] = False
                        continue
                    else:
                        # The main thread will take care of aborting the application.
                        results[n] = ex
                        return
                finally:
                    with condition:
                        condition.notify()
            with condition:
                assert results[n] is True
                results[n] = result
                condition.notify()

    def _process(self, path, pages=None):
        try:
            self._engine = self._options.engine()
        except errors.EngineNotFound, ex:
            print >>sys.stderr, ex
            sys.exit(1)
        print >>sys.stderr, 'Processing %s:' % utils.smart_repr(path, system_encoding)
        document = self.new_document(djvu.decode.FileURI(path))
        document.decoding_job.wait()
        if pages is None:
            pages = list(document.pages)
        else:
            pages = [document.pages[i - 1] for i in pages]
        results = dict((page.n, None) for page in pages)
        condition = threading.Condition()
        threads = [
            threading.Thread(target=self.page_thread, args=(pages, results, condition))
            for i in xrange(self._options.n_jobs)
        ]
        for thread in threads:
            thread.start()
        sed_file = self._temp_file('ocrodjvu.djvused', auto_remove=False)
        try:
            if self._options.clear_text:
                sed_file.write('remove-txt\n')
            for page in pages:
                file_id = page.file.id.encode(system_encoding)
                sed_file.write('select \'%s\'\n' % file_id.replace('\\', '\\\\').replace("'", "\\'"))
                sed_file.write('set-txt\n')
                with condition:
                    while 1:
                        result = results[page.n]
                        if result is None or result is True:
                            # Result is not yet available.
                            condition.wait()
                        elif isinstance(result, Exception):
                            for thread in threads:
                                thread.join()
                            self._debug = True
                            sys.exit(1)
                        else:
                            break
                if result is False:
                    # No image suitable for OCR.
                    pass
                else:
                    result.print_into(sed_file)
                sed_file.write('\n.\n\n')
            sed_file.flush()
            saver = self._options.saver
            if saver.in_place:
                document = None
            pages_to_save = None
            if self._options.ocr_only:
                pages_to_save = [page.n for page in pages]
            self._options.saver.save(document, pages_to_save, path, sed_file)
            document = None
        finally:
            sed_file.close()

    def process(self, *args, **kwargs):
        try:
            self._process(*args, **kwargs)
        except:
            # The djvused script can be valuable and should not be lost in case
            # of crash.
            self._debug = True
            raise

    def close(self):
        if self._debug:
            return self._temp_dir
        else:
            shutil.rmtree(self._temp_dir)

def main():
    options = ArgumentParser().parse_args()
    context = Context()
    context.init(options)
    try:
        context.process(options.path, options.pages)
    finally:
        temp_dir = context.close()
        if temp_dir is not None:
            print >>sys.stderr, 'Intermediate files were left in the %r directory.' % temp_dir

# vim:ts=4 sw=4 et