#!/usr/bin/env python

from __future__ import print_function

try:
    # Python 2
    import urlparse
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from StringIO import StringIO
    from urllib import unquote
except ImportError:
    # Python 3
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from io import StringIO
    import urllib.parse as urlparse
    from urllib.parse import unquote

import os.path
import pstats
import re
import sys
import traceback
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Match, Optional, Tuple
    FuncType = Tuple[str, str, str]


PORT = 4040

DIR = os.path.dirname(os.path.realpath(__file__))

INDEX_PAGE_HTML = open(os.path.join(DIR, 'html/index.html')).read()

FUNCTION_PAGE_HTML = open(os.path.join(DIR, 'html/function.html')).read()


def htmlquote(fn):
    return fn.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def shrink(s):
    if len(s) < 40:
        return s
    return s[:20] + '...' + s[-20:]


def formatfunc(func):
    # type: (FuncType) -> str
    file, line, func_name = func
    containing_dir = os.path.basename(os.path.dirname(file).rstrip('/'))
    return '%s:%s:%s' % (os.path.join(containing_dir, os.path.basename(file)),
                         line, htmlquote(shrink(func_name)))


def wrapTag(tag, body, **kwargs):
    # type: (str, str, **str) -> str
    attrs = ''
    if kwargs:
        attrs = ' ' + ' '.join('%s="%s"' % (key, value)
                               for key, value in kwargs.items())
    open_tag = '<%s%s>' % (tag, attrs)
    return '%s%s</%s>' % (open_tag, body, tag)


def formatTime(dt):
    # type: (float) -> str
    return '%.2fs' % dt


def formatTimeAndPercent(dt, total):
    # type: (float, float) -> str
    percent = '(%.1f%%)' % (100.0 * dt / total)
    if percent == '(0.0%)':
        percent = ''
    return '%s&nbsp;%s' % (
        formatTime(dt), wrapTag('font', percent, color='#808080'))


class MyHandler(BaseHTTPRequestHandler):
    def __init__(self, stats, *args, **kwargs):
        # type: (pstats.Stats, *Any, **Any) -> None
        self.stats = stats
        self.stats.calc_callees()
        self.total_time = self.stats.total_tt
        (self.filename,) = self.stats.files
        self.width, self.print_list = self.stats.get_print_list(())

        self.func_to_id = {}
        self.id_to_func = {}

        for i, func in enumerate(self.print_list):
            self.id_to_func[i] = func
            self.func_to_id[func] = i

        self.routes = self.setup_routes()
        BaseHTTPRequestHandler.__init__(self, *args, **kwargs)

    def setup_routes(self):
        # type: () -> Dict[str, Callable]
        routes = {}
        for method_name in dir(self):
            method = getattr(self, method_name)
            if method.__doc__ is None:
                continue
            if method.__doc__.startswith('handle:'):
                _handle, path_re = method.__doc__.split(':')
                path_re = path_re.strip()
                routes[path_re] = method
        return routes

    def _find_handler(self, path):
        # type: (str) -> Tuple[Optional[Callable], Optional[Match[str]]]
        for path_re, method in self.routes.items():
            match_obj = re.match(path_re, path)
            if match_obj is None:
                print('did not handle %s with %s' % (path, path_re))
                continue
            print('handling %s with %s (%s)' % (
                path, path_re, match_obj.groups()))
            return method, match_obj

        # This can happen for things like /favicon.ico, so we can't just abort
        print('no handler for %s' % path)
        return None, None


    def _filter_exp_from_query(self):
        # type: () -> Optional[str]
        filter_exp = self.query.get('filter', None)
        if filter_exp:
            filter_exp = unquote(filter_exp)
        return filter_exp

    def _filter_query_from_exp(self, filter_exp):
        # type: (Optional[str]) -> str
        return '?filter={}'.format(filter_exp) if filter_exp else ''

    def get_function_link(self, func, filter_exp):
        # type: (FuncType, Optional[str]) -> str
        _file, _line, func_name = func
        title = func_name
        func_id = self.func_to_id[func]
        filter_query = self._filter_query_from_exp(filter_exp)

        return wrapTag(
            'a', formatfunc(func), title=title, href='/func/{func_id}{filter_query}'.format(
                func_id=func_id, filter_query=filter_query))

    def do_GET(self):
        # type: () -> None
        path, query = urlparse.urlsplit(self.path)[2:4]
        self.query = {}
        for elt in query.split('&'):
            if not elt:
                continue
            key, value = elt.split('=', 1)
            self.query[key] = value

        method, mo = self._find_handler(path)
        if not method:
            self.send_response(404)
            return

        assert mo is not None   # mypy

        try:
            temp = StringIO()
            original_wfile = self.wfile
            self.wfile = temp
            try:
                method(*mo.groups())
            finally:
                self.wfile = original_wfile

            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(temp.getvalue().encode('utf8'))
        except Exception:
            self.send_response(500)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(traceback.format_exc().encode('utf8'))

    def index(self):
        # type: () -> None
        'handle: /$'
        table = []

        sort_index = ['cc', 'nc', 'tt', 'ct', 'epc', 'ipc'].index(
            self.query.get('sort', 'ct'))
        print('sort_index', sort_index)

        # EPC/IPC (exclusive/inclusive per call) are fake fields that need to
        # be calculated
        if sort_index < 4:
            self.print_list.sort(
                key=lambda func: self.stats.stats[func][sort_index],
                reverse=True)
        elif sort_index == 4:     # EPC
            self.print_list.sort(
                key=lambda func: (
                    self.stats.stats[func][2] / self.stats.stats[func][0]),
                reverse=True
            )
        elif sort_index == 5:   # IPC
            self.print_list.sort(
                key=lambda func: (
                    self.stats.stats[func][3] / self.stats.stats[func][0]),
                reverse=True
            )
        else:
            # Shouldn't get here
            raise ValueError('Invalid sort_index: {}'.format(sort_index))


        filter_exp = self._filter_exp_from_query()
        if filter_exp:
            print('filter_exp:', filter_exp)
        for func in self.print_list:
            if filter_exp and not re.search(filter_exp, formatfunc(func)):
                continue
            (primitive_calls, total_calls,
             exclusive_time, inclusive_time, callers) = self.stats.stats[func]

            row = wrapTag('tr', ''.join(
                wrapTag('td', cell) for cell in (
                    self.get_function_link(func, filter_exp),
                    formatTimeAndPercent(exclusive_time, self.total_time),
                    formatTimeAndPercent(inclusive_time, self.total_time),
                    primitive_calls,
                    total_calls,
                    formatTime(exclusive_time / (primitive_calls or 1)),
                    formatTime(inclusive_time / (primitive_calls or 1)))))

            table.append(row)

        data = INDEX_PAGE_HTML.format(
            filename=self.filename, total_time=formatTime(self.total_time),
            filter_exp=filter_exp or '',
            filter_param=('&filter=%s' % filter_exp) if filter_exp else '',
            table='\n'.join(table))
        self.wfile.write(data)

    def func(self, func_id_str):
        # type: (str) -> None
        'handle: /func/(.*)$'
        # func_id_str may also include query params

        func_id = int(func_id_str)
        func = self.id_to_func[func_id]
        filter_exp = self._filter_exp_from_query()

        f_cc, f_nc, f_tt, f_ct, callers = self.stats.stats[func]
        callees = self.stats.all_callees[func]

        def sortedByInclusive(items):
            sortable = [(ct, (f, (cc, nc, tt, ct)))
                        for f, (cc, nc, tt, ct) in items]
            return [y for x, y in sorted(sortable, reverse=True)]

        def build_function_table(items, filter_exp):
            # type: (List, Optional[str]) -> str
            callersTable = []
            for caller, (cc, nc, tt, ct) in sortedByInclusive(items):
                tag = wrapTag(
                    'tr', ''.join(
                        wrapTag('td', cell)
                        for cell in (
                            self.get_function_link(caller, filter_exp),
                            formatTimeAndPercent(tt, self.total_time),
                            formatTimeAndPercent(ct, self.total_time),
                            cc,
                            nc,
                            # ncalls shouldn't be 0, but I guess it can be
                            formatTime(tt / (cc or 1)),
                            formatTime(ct / (cc or 1)))))
                callersTable.append(tag)
            return '\n'.join(callersTable)

        caller_stats = [(c, self.stats.stats[c][:4]) for c in callers]
        callersTable = build_function_table(caller_stats, filter_exp)
        calleesTable = build_function_table(callees.items(), filter_exp)
        selfTable = build_function_table([(func, (f_cc, f_nc, f_tt, f_ct))], filter_exp)

        page = FUNCTION_PAGE_HTML.format(
            filter_query=self._filter_query_from_exp(filter_exp),
            func=formatfunc(func), primitive=f_cc, total=f_nc,
            exclusive=f_tt, inclusive=f_ct, callers=callersTable, self=selfTable,
            callees=calleesTable)

        self.wfile.write(page)


def main(argv):
    # type: (List[str]) -> None
    statsfile = argv[1]
    port = argv[2:]
    if port == []:
        port = PORT
    else:
        port = int(port[0])

    stats = pstats.Stats(statsfile)

    httpd = HTTPServer(
        ('', port),
        lambda *a, **kwargs: MyHandler(stats, *a, **kwargs))
    httpd.serve_forever()


if __name__ == '__main__':
    main(argv=sys.argv)
