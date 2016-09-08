#!/usr/bin/env python

from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
from StringIO import StringIO

import os.path
import pstats
import sys
import re
import traceback
import urllib
import urlparse

PORT = 4040

DIR = os.path.dirname(__file__)

INDEX_PAGE_HTML = open(os.path.join(DIR, 'html/index.html')).read()

FUNCTION_PAGE_HTML = open(os.path.join(DIR, 'html/function.html')).read()


def htmlquote(fn):
    return fn.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def shrink(s):
    if len(s) < 40:
        return s
    return s[:20] + '...' + s[-20:]


def formatfunc(func):
    file, line, func_name = func
    containing_dir = os.path.basename(os.path.dirname(file).rstrip('/'))
    return '%s:%s:%s' % (os.path.join(containing_dir, os.path.basename(file)),
                         line, htmlquote(shrink(func_name)))


def wrapTag(tag, body, **kwargs):
    attrs = ''
    if kwargs:
        attrs = ' ' + ' '.join('%s="%s"' % (key, value)
                               for key, value in kwargs.iteritems())
    open_tag = '<%s%s>' % (tag, attrs)
    return '%s%s</%s>' % (open_tag, body, tag)


def formatTime(dt):
    return '%.2fs' % dt


def formatTimeAndPercent(dt, total):
    percent = '(%.1f%%)' % (100.0 * dt / total)
    if percent == '(0.0%)':
        percent = ''
    return '%s&nbsp;%s' % (
        formatTime(dt), wrapTag('font', percent, color='#808080'))


class MyHandler(BaseHTTPRequestHandler):
    def __init__(self, stats=None, *args, **kw):
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
        BaseHTTPRequestHandler.__init__(self, *args, **kw)

    def setup_routes(self):
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
        for path_re, method in self.routes.iteritems():
            match_obj = re.match(path_re, path)
            if match_obj is None:
                print 'did not handle %s with %s' % (path, path_re)
                continue
            print 'handling %s with %s (%s)' % (
                path, path_re, match_obj.groups())
            return method, match_obj
        print 'no handler for %s' % path
        return None, None

    def getFunctionLink(self, func):
        _file, _line, func_name = func
        title = func_name
        func_id = self.func_to_id[func]

        return wrapTag(
            'a', formatfunc(func), title=title, href='/func/%s' % func_id)

    def do_GET(self):
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
            self.wfile.write(temp.getvalue())
        except Exception:
            self.send_response(500)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            traceback.print_exc(file=self.wfile)

    def index(self):
        'handle: /$'
        table = []

        sort_index = ['cc', 'nc', 'tt', 'ct', 'epc', 'ipc'].index(
            self.query.get('sort', 'ct'))
        print 'sort_index', sort_index

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
            pass

        filter_exp = self.query.get('filter', '')
        if filter_exp:
            filter_exp = urllib.unquote(filter_exp)
            print 'filter_exp:', filter_exp
        for func in self.print_list:
            if filter_exp and not re.search(filter_exp, formatfunc(func)):
                continue
            (primitive_calls, total_calls,
             exclusive_time, inclusive_time, callers) = self.stats.stats[func]

            row = wrapTag('tr', ''.join(
                wrapTag('td', cell) for cell in (
                    self.getFunctionLink(func),
                    formatTimeAndPercent(exclusive_time, self.total_time),
                    formatTimeAndPercent(inclusive_time, self.total_time),
                    primitive_calls,
                    total_calls,
                    formatTime(exclusive_time / (primitive_calls or 1)),
                    formatTime(inclusive_time / (primitive_calls or 1)))))

            table.append(row)

        data = INDEX_PAGE_HTML.format(
            filename=self.filename, total_time=formatTime(self.total_time),
            filter_exp=filter_exp,
            filter_param=('&filter=%s' % filter_exp) if filter_exp else '',
            table='\n'.join(table))
        self.wfile.write(data)

    def func(self, id):
        'handle: /func/(.*)$'
        func_id = int(id)
        func = self.id_to_func[func_id]

        f_cc, f_nc, f_tt, f_ct, callers = self.stats.stats[func]
        callees = self.stats.all_callees[func]

        def sortedByInclusive(items):
            sortable = [(ct, (f, (cc, nc, tt, ct)))
                        for f, (cc, nc, tt, ct) in items]
            return [y for x, y in sorted(sortable, reverse=True)]

        def buildFunctionTable(items):
            callersTable = []
            for caller, (cc, nc, tt, ct) in sortedByInclusive(items):
                tag = wrapTag(
                    'tr', ''.join(
                        wrapTag('td', cell)
                        for cell in (
                            self.getFunctionLink(caller),
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
        callersTable = buildFunctionTable(caller_stats)
        calleesTable = buildFunctionTable(callees.items())

        page = FUNCTION_PAGE_HTML.format(
            func=formatfunc(func), primitive=f_cc, total=f_nc, exclusive=f_tt,
            inclusive=f_ct, callers=callersTable, callees=calleesTable)

        self.wfile.write(page)


def main(argv):
    statsfile = argv[1]
    port = argv[2:]
    if port == []:
        port = PORT
    else:
        port = int(port[0])

    stats = pstats.Stats(statsfile)

    httpd = HTTPServer(
        ('', port),
        lambda *a, **kw: MyHandler(stats, *a, **kw))
    httpd.serve_forever()


if __name__ == '__main__':
    main(argv=sys.argv)
