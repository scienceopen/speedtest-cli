#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2012-2015 Matt Martz
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
from __future__ import print_function
import os
import re
import sys
import math
import signal
import socket
import timeit
import platform
import threading
#
from speedtest_cli.parsecmd import parsecmd

__version__ = '0.3.4'

# Some global variables we use
user_agent = None
source = None
shutdown_event = None
scheme = 'http'


# Used for bound_interface
socket_socket = socket.socket

try:
    import xml.etree.cElementTree as ET
except ImportError:
    try:
        import xml.etree.ElementTree as ET
    except ImportError:
        from xml.dom import minidom as DOM
        ET = None

from six.moves.urllib.request import urlopen, Request
from six.moves.urllib.error import HTTPError, URLError

from six.moves.http_client import HTTPConnection, HTTPSConnection

from six.moves.queue import Queue

from six.moves.urllib.parse import urlparse, parse_qs

try:
    from hashlib import md5
except ImportError:
    from md5 import md5

class SpeedtestCliServerListError(Exception):
    """Internal Exception class used to indicate to move on to the next
    URL for retrieving speedtest.net server details

    """


def bound_socket(*args, **kwargs):
    """Bind socket to a specified source IP address"""

    global source
    sock = socket_socket(*args, **kwargs)
    sock.bind((source, 0))
    return sock


def distance(origin, destination):
    """Determine distance between 2 sets of [lat,lon] in km"""

    lat1, lon1 = origin
    lat2, lon2 = destination
    radius = 6371  # km

    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) * math.sin(dlat / 2) +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) * math.sin(dlon / 2) *
         math.sin(dlon / 2))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    d = radius * c

    return d


def build_user_agent():
    """Build a Mozilla/5.0 compatible User-Agent string"""

    global user_agent
    if user_agent:
        return user_agent

    ua_tuple = (
        'Mozilla/5.0',
        '(%s; U; %s; en-us)' % (platform.system(), platform.architecture()[0]),
        'Python/%s' % platform.python_version(),
        '(KHTML, like Gecko)',
        'speedtest-cli/%s' % __version__
    )
    user_agent = ' '.join(ua_tuple)
    return user_agent


def build_request(url, data=None, headers={}):
    """Build a urllib2 request object

    This function automatically adds a User-Agent header to all requests

    """

    if url[0] == ':':
        schemed_url = '%s%s' % (scheme, url)
    else:
        schemed_url = url

    headers['User-Agent'] = user_agent
    return Request(schemed_url, data=data, headers=headers)


def catch_request(request):
    """Helper function to catch common exceptions encountered when
    establishing a connection with a HTTP/HTTPS request

    """

    try:
        uh = urlopen(request)
        return uh, False
    except (HTTPError, URLError, socket.error):
        e = sys.exc_info()[1]
        return None, e


class FileGetter(threading.Thread):
    """Thread class for retrieving a URL"""

    def __init__(self, url, start):
        self.url = url
        self.result = None
        self.starttime = start
        threading.Thread.__init__(self)

    def run(self):
        self.result = [0]
        try:
            if (timeit.default_timer() - self.starttime) <= 10:
                request = build_request(self.url)
                f = urlopen(request)
                while 1 and not shutdown_event.isSet():
                    self.result.append(len(f.read(10240)))
                    if self.result[-1] == 0:
                        break
                f.close()
        except IOError:
            pass


def downloadSpeed(files, quiet=False):
    """Function to launch FileGetter threads and calculate download speeds"""

    start = timeit.default_timer()

    def producer(q, files):
        for file in files:
            thread = FileGetter(file, start)
            thread.start()
            q.put(thread, True)
            if not quiet and not shutdown_event.isSet():
                sys.stdout.write('.')
                sys.stdout.flush()

    finished = []

    def consumer(q, total_files):
        while len(finished) < total_files:
            thread = q.get(True)
            while thread.isAlive():
                thread.join(timeout=0.1)
            finished.append(sum(thread.result))
            del thread

    q = Queue(6)
    prod_thread = threading.Thread(target=producer, args=(q, files))
    cons_thread = threading.Thread(target=consumer, args=(q, len(files)))
    start = timeit.default_timer()
    prod_thread.start()
    cons_thread.start()
    while prod_thread.isAlive():
        prod_thread.join(timeout=0.1)
    while cons_thread.isAlive():
        cons_thread.join(timeout=0.1)
    return (sum(finished) / (timeit.default_timer() - start))


class FilePutter(threading.Thread):
    """Thread class for putting a URL"""

    def __init__(self, url, start, size):
        self.url = url
        chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        data = chars * (int(round(int(size) / 36.0)))
        self.data = ('content1=%s' % data[0:int(size) - 9]).encode()
        del data
        self.result = None
        self.starttime = start
        threading.Thread.__init__(self)

    def run(self):
        try:
            if ((timeit.default_timer() - self.starttime) <= 10 and
                    not shutdown_event.isSet()):
                request = build_request(self.url, data=self.data)
                f = urlopen(request)
                f.read(11)
                f.close()
                self.result = len(self.data)
            else:
                self.result = 0
        except IOError:
            self.result = 0


def uploadSpeed(url, sizes, quiet=False):
    """Function to launch FilePutter threads and calculate upload speeds"""

    start = timeit.default_timer()

    def producer(q, sizes):
        for size in sizes:
            thread = FilePutter(url, start, size)
            thread.start()
            q.put(thread, True)
            if not quiet and not shutdown_event.isSet():
                sys.stdout.write('.')
                sys.stdout.flush()

    finished = []

    def consumer(q, total_sizes):
        while len(finished) < total_sizes:
            thread = q.get(True)
            while thread.isAlive():
                thread.join(timeout=0.1)
            finished.append(thread.result)
            del thread

    q = Queue(6)
    prod_thread = threading.Thread(target=producer, args=(q, sizes))
    cons_thread = threading.Thread(target=consumer, args=(q, len(sizes)))
    start = timeit.default_timer()
    prod_thread.start()
    cons_thread.start()
    while prod_thread.isAlive():
        prod_thread.join(timeout=0.1)
    while cons_thread.isAlive():
        cons_thread.join(timeout=0.1)
    return (sum(finished) / (timeit.default_timer() - start))


def getAttributesByTagName(dom, tagName):
    """Retrieve an attribute from an XML document and return it in a
    consistent format

    Only used with xml.dom.minidom, which is likely only to be used
    with python versions older than 2.5
    """
    elem = dom.getElementsByTagName(tagName)[0]
    return dict(list(elem.attributes.items()))


def getConfig():
    """Download the speedtest.net configuration and return only the data
    we are interested in
    """

    request = build_request('://www.speedtest.net/speedtest-config.php')
    uh, e = catch_request(request)
    if e:
        raise RuntimeError('Could not retrieve speedtest.net configuration: %s' % e)

    configxml = []
    while 1:
        configxml.append(uh.read(10240))
        if len(configxml[-1]) == 0:
            break
    if int(uh.code) != 200:
        return None
    uh.close()
    try:
        try:
            root = ET.fromstring(''.encode().join(configxml))
            config = {
                'client': root.find('client').attrib,
                'times': root.find('times').attrib,
                'download': root.find('download').attrib,
                'upload': root.find('upload').attrib}
        except AttributeError:  # Python3 branch
            root = DOM.parseString(''.join(configxml))
            config = {
                'client': getAttributesByTagName(root, 'client'),
                'times': getAttributesByTagName(root, 'times'),
                'download': getAttributesByTagName(root, 'download'),
                'upload': getAttributesByTagName(root, 'upload')}
    except SyntaxError:
        raise RuntimeError('Failed to parse speedtest.net configuration')

    del root
    del configxml
    return config


def closestServers(client, all=False):
    """Determine the 5 closest speedtest.net servers based on geographic
    distance
    """

    urls = [
        '://www.speedtest.net/speedtest-servers-static.php',
        '://c.speedtest.net/speedtest-servers-static.php',
        '://www.speedtest.net/speedtest-servers.php',
        '://c.speedtest.net/speedtest-servers.php',
    ]
    errors = []
    servers = {}
    for url in urls:
        try:
            request = build_request(url)
            uh, e = catch_request(request)
            if e:
                errors.append('%s' % e)
                raise SpeedtestCliServerListError
            serversxml = []
            while 1:
                serversxml.append(uh.read(10240))
                if len(serversxml[-1]) == 0:
                    break
            if int(uh.code) != 200:
                uh.close()
                raise SpeedtestCliServerListError
            uh.close()
            try:
                try:
                    root = ET.fromstring(''.encode().join(serversxml))
                    elements = root.getiterator('server')
                except AttributeError:  # Python3 branch
                    root = DOM.parseString(''.join(serversxml))
                    elements = root.getElementsByTagName('server')
            except SyntaxError:
                raise SpeedtestCliServerListError
            for server in elements:
                try:
                    attrib = server.attrib
                except AttributeError:
                    attrib = dict(list(server.attributes.items()))
                d = distance([float(client['lat']),
                              float(client['lon'])],
                             [float(attrib.get('lat')),
                              float(attrib.get('lon'))])
                attrib['d'] = d
                if d not in servers:
                    servers[d] = [attrib]
                else:
                    servers[d].append(attrib)
            del root
            del serversxml
            del elements
        except SpeedtestCliServerListError:
            continue

        # We were able to fetch and parse the list of speedtest.net servers
        if servers:
            break

    if not servers:
        raise RuntimeError('Failed to retrieve list of speedtest.net servers:\n\n %s' %
               '\n'.join(errors))

    closest = []
    for d in sorted(servers.keys()):
        for s in servers[d]:
            closest.append(s)
            if len(closest) == 5 and not all:
                break
        else:
            continue
        break

    del servers
    return closest


def getBestServer(servers):
    """Perform a speedtest.net latency request to determine which
    speedtest.net server has the lowest latency
    """

    results = {}
    for server in servers:
        cum = []
        url = '%s/latency.txt' % os.path.dirname(server['url'])
        urlparts = urlparse(url)
        for i in range(0, 3):
            try:
                if urlparts[0] == 'https':
                    h = HTTPSConnection(urlparts[1])
                else:
                    h = HTTPConnection(urlparts[1])
                headers = {'User-Agent': user_agent}
                start = timeit.default_timer()
                h.request("GET", urlparts[2], headers=headers)
                r = h.getresponse()
                total = (timeit.default_timer() - start)
            except (HTTPError, URLError, socket.error):
                cum.append(3600)
                continue
            text = r.read(9)
            if int(r.status) == 200 and text == 'test=test'.encode():
                cum.append(total)
            else:
                cum.append(3600)
            h.close()
        avg = round((sum(cum) / 6) * 1000, 3)
        results[avg] = server
    fastest = sorted(results.keys())[0]
    best = results[fastest]
    best['latency'] = fastest

    return best


def ctrl_c(signum, frame):
    """Catch Ctrl-C key sequence and set a shutdown_event for our threaded
    operations
    """

    global shutdown_event
    shutdown_event.set()
    raise SystemExit('\nCancelling...')


def version():
    """Print the version"""
    raise SystemExit(__version__)

def estimatespeed(args,best):
    sizes = [350, 500, 750, 1000, 1500, 2000, 2500, 3000, 3500, 4000]
    urls = []
    for size in sizes:
        for i in range(0, 4):
            urls.append('%s/random%sx%s.jpg' %
                        (os.path.dirname(best['url']), size, size))
    if not args.simple:
        print('Testing download speed', end='')
    dlspeed = downloadSpeed(urls, args.simple)
    if not args.simple:
        print()
    print('Download: %0.2f M%s/s' %
           ((dlspeed / 1000 / 1000) * args.units[1], args.units[0]))

    sizesizes = [int(.25 * 1000 * 1000), int(.5 * 1000 * 1000)]
    sizes = []
    for size in sizesizes:
        for i in range(0, 25):
            sizes.append(size)
    if not args.simple:
        print('Testing upload speed', end='')
    ulspeed = uploadSpeed(best['url'], sizes, args.simple)
    if not args.simple:
        print()
    print('Upload: %0.2f M%s/s' %
           ((ulspeed / 1000 / 1000) * args.units[1], args.units[0]))


    dlspeedk = int(round((dlspeed / 1000) * 8, 0))
    ping = int(round(best['latency'], 0))
    ulspeedk = int(round((ulspeed / 1000) * 8, 0))

    return dlspeedk,ulspeedk,ping

def speedtest():
    """Run the full speedtest.net test"""

    global shutdown_event, source, scheme
    shutdown_event = threading.Event()

    signal.signal(signal.SIGINT, ctrl_c)

    args = parsecmd()

    socket.setdefaulttimeout(args.timeout)

    # Pre-cache the user agent string
    build_user_agent()

    # If specified bind to a specific IP address
    if args.source:
        source = args.source
        socket.socket = bound_socket

    if args.secure:
        scheme = 'https'

    if not args.simple:
        print('Retrieving speedtest.net configuration...')
    try:
        config = getConfig()
    except URLError:
        raise RuntimeError('Cannot retrieve speedtest configuration')

    if not args.simple:
        print('Retrieving speedtest.net server list...')
    if args.list or args.server:
        servers = closestServers(config['client'], True)
        if args.list:
            serverList = []
            for server in servers:
                line = ('%(id)4s) %(sponsor)s (%(name)s, %(country)s) '
                        '[%(d)0.2f km]' % server)
                serverList.append(line)
            print('\n'.join(serverList).encode('utf-8', 'ignore'))
            return
    else:
        servers = closestServers(config['client'])

    if not args.simple:
        print('Testing from %(isp)s (%(ip)s)...' % config['client'])

    if args.server:
        try:
            best = getBestServer([x for x in servers if x['id'] == args.server])
        except IndexError:
            raise RuntimeError('Invalid server ID')

    elif args.mini:
        name, ext = os.path.splitext(args.mini)
        if ext:
            url = os.path.dirname(args.mini)
        else:
            url = args.mini
        urlparts = urlparse(url)
        try:
            request = build_request(args.mini)
            f = urlopen(request)
        except Exception as e:
            print(e)
            raise ValueError('Invalid Speedtest Mini URL')

        else:
            text = f.read()
            f.close()
        extension = re.findall('upload_extension: "([^"]+)"', text.decode())
        if not extension:
            for ext in ['php', 'asp', 'aspx', 'jsp']:
                try:
                    request = build_request('%s/speedtest/upload.%s' %
                                            (args.mini, ext))
                    f = urlopen(request)
                except Exception as e:
                    print(e)
                else:
                    data = f.read().strip()
                    if (f.code == 200 and
                            len(data.splitlines()) == 1 and
                            re.match('size=[0-9]', data)):
                        extension = [ext]
                        break
        if not urlparts or not extension:
            raise ValueError('Please provide the full URL of your Speedtest Mini server')

        servers = [{
            'sponsor': 'Speedtest Mini',
            'name': urlparts[1],
            'd': 0,
            'url': '%s/speedtest/upload.%s' % (url.rstrip('/'), extension[0]),
            'latency': 0,
            'id': 0
        }]
        try:
            best = getBestServer(servers)
        except Exception as e:
            print(e)
            best = servers[0]
    else:
        if not args.simple:
            print('Selecting best server based on latency...')
        best = getBestServer(servers)

    if not args.simple:
        print(('Hosted by %(sponsor)s (%(name)s) [%(d)0.2f km]: '
               '%(latency)s ms' % best).encode('utf-8', 'ignore'))
    else:
        print('Ping: %(latency)s ms' % best)

    dlspeedk,ulspeedk,ping = estimatespeed(args,best)

    if args.share:
        if args.share and args.mini:
            raise TypeError('Cannot generate a speedtest.net share results image while '
                             'testing against a Speedtest Mini server')

        uploadResult(dlspeedk,ulspeedk,ping)

    return dlspeedk,ulspeedk,ping,best

def uploadResult(dlspeedk,ulspeedk,ping):
    """
    Build the request to send results back to speedtest.net
    We use a list instead of a dict because the API expects parameters
    in a certain order
    """
    apiData = [
        'download=%s' % dlspeedk,
        'ping=%s' % ping,
        'upload=%s' % ulspeedk,
        'promo=',
        'startmode=%s' % 'pingselect',
        'recommendedserverid=%s' % best['id'],
        'accuracy=%s' % 1,
        'serverid=%s' % best['id'],
        'hash=%s' % md5(('%s-%s-%s-%s' %
                         (ping, ulspeedk, dlspeedk, '297aae72'))
                        .encode()).hexdigest()]

    headers = {'Referer': 'http://c.speedtest.net/flash/speedtest.swf'}
    request = build_request('://www.speedtest.net/api/api.php',
                            data='&'.join(apiData).encode(),
                            headers=headers)
    f, e = catch_request(request)
    if e:
        raise RuntimeError('Could not submit results to speedtest.net: %s' % e)

    response = f.read()
    code = f.code
    f.close()

    if int(code) != 200:
        raise RuntimeError('Could not submit results to speedtest.net')

    qsargs = parse_qs(response.decode())
    resultid = qsargs.get('resultid')
    if not resultid or len(resultid) != 1:
        raise RuntimeError('Could not submit results to speedtest.net')

    print('Share results: %s://www.speedtest.net/result/%s.png' %
           (scheme, resultid[0]))

def main():
    try:
        speedtest()
    except KeyboardInterrupt:
        print('\nCancelling...')


if __name__ == '__main__':
    main()

# vim:ts=4:sw=4:expandtab
