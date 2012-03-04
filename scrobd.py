#!/usr/bin/env python
# -*- coding: utf-8 -*-

# multi-scrobbler for MPD
# Copyright (C) 2008  Jean-Philippe Brucker <jayx00@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# TODO :
# - multiple submission when sendall => change scrobbler2 for this
# - logging module
# - optparse instead of getopt and the ugly Config class.

from __future__ import with_statement

__author__   = "jeanbon"
__version__  = "0.3"
__revision__ = "$Id: scrobd.py 38 2011-02-21 19:38:14Z chickenzilla $"

import os
import sys
import time
import inspect
from getpass import getpass
from hashlib import md5
from getopt import getopt, GetoptError
from urllib2 import URLError
from base64 import b64encode, b64decode
from threading import Thread
from socket import error as SocketError
from socket import timeout as SocketTimeout
from httplib import BadStatusLine


import scrobbler2
import mpd
try:
    import musicbrainz2.webservice as musicbrainz
except:
    musicbrainz = None

import locale
import codecs
from unicodedata import normalize

CLIENT_ID = "srd"
CLIENT_VERSION = __version__
DEBUG = {
     "ScrobblerConnexion.connection_handler":   False,
     "ScrobblerConnexion.send":                 False,
     "ScrobblerConnexion._submit":              False,
     "ScrobblerConnexion._connect":             False,
     "ScrobblerConnexion.init":                 False,
     "ScrobblerConnexion.sendall":              False,
     "ScrobblersQueue.send":                    False,
     "ScrobblersQueue.now_playing":             False,
     "ScrobblersQueue.put":                     False,
     "musicbrainz":                             False
}


class MPDInfos(mpd.MPDClient): #{{{
    def __init__(self, host, port, password, log_info, log_error):
        mpd.MPDClient.__init__(self)
        self._host = host
        self._port = port
        self._password = password
        self.log_error = log_error
        self.log_info = log_info
        self.connected = True

    def get_song(self, inner_call=False):
        """
        Return a dict : (album, composer, title, track, artist, pos,
        genre, file, time, date, id)
        """
        if not self.connection_handler():
            return Song()
        try:
            currentsong = self.currentsong()
            for key in currentsong:
                if isinstance(currentsong[key], (list,tuple)):
                    # Mutiple artist ? wtf ?
                    # Only happend with Led Zep, the battle of evermore
                    currentsong[key] = currentsong[key][0]
                currentsong[key] = unicode2ascii(currentsong[key])
            try:
                return Song(currentsong)
            except KeyError:
                return Song()
        except mpd.MPDError, SocketTimeout:
            if not inner_call:
                self.log_error(_("No response from the MPD server :("))
                self.log_info(_("Trying to reconnect... \n"))
            ret = self.reconnect()
            if ret:
                self.log_error(_("FAIL : %s") % ret)
                self.connected = False
            elif not inner_call:
                return self.get_song(inner_call=True)
            else:
                self.connected = False
        return Song()
    
    def connect(self):
        """
        Connexion au serveur mpd
        """
        try:
            mpd.MPDClient.connect(self, self._host, self._port)
            if self._password:
                self.__getattr__('password')(self._password)
            self.connected = True
            return True
        except (SocketError, SocketTimeout), err:
            self.connected = False
            self.log_error(_(u"Unable to connect to the MPD server : %s") %
                    err)
        return False

    def connection_handler(self):
        if not self.connected:
            self.reconnect()
            if self.connected:
                return True
            return False
        return True

    def reconnect(self):
        try:
            self.disconnect()
        except mpd.ConnectionError:
            pass
        try:
            self.connect()
        except mpd.MPDError, err:
            # FAIL FAIL FAIL
            self.connected = False
            if err.find("Already connected") == -1:
                return err
            print err
        except SocketTimeout:
            self.connected = False
        return ""

    def get_time(self, inner_call=False):
        """
        Return a tuple :
            0 => time elapsed
            1 => total time
            2 => percentage
        """
        if not self.connection_handler():
            return (0, 0, 0)
        try:
            elapsed, total = [int(a) for a in
                    self.__getattr__('status')()['time'].split(':', 1)]
            self.connected = True
            return (elapsed, total, elapsed * 100 / total if total != 0 else 0)
        except KeyError:
            return (0, 0, 0)
        except mpd.MPDError:
            if not inner_call:
                self.log_error(_("No response from the MPD server :("))
                self.log_info(_("Trying to reconnect... \n"))
            ret = self.reconnect()
            if ret:
                self.log_error(_("FAIL : %s") % ret)
            elif not inner_call:
                return self.get_time(inner_call=True)
        self.connected = False
        return (0, 0, 0)
#}}}


class ScrobblersQueue: #{{{
    def __init__(self, scrobblers, config, log_info, log_error):
        self.config = config
        self.scrobblers = scrobblers
        self.log_error = log_error
        self.log_info = log_info

    def connected(self):
        """
        Returns the number of connected scrobblers
        """
        return sum(int(scrob.next_connexion) == -1 for scrob in self.scrobblers)

    def send(self):
        """
        Returns the number of connected scrobblers
        """
        connected = 0
        short = False
        for scrob in self.scrobblers:
            debug("sending to scrobbler", scrob)
            ret = scrob.send()
            debug("return : ", ret)
            if ret == 2:
                short = True
            if scrob.next_connexion == -1:
                connected += 1
        if short:
            self.log_info(_("Not submitted (Not played enough or too"
                       " short)\n"), date=False)
        debug("returning : ", connected)
        return connected

    def sendall(self):
        """
        Try to send everything in the queue
        If fail, returns False
        """
        for scrob in self.scrobblers:
            scrob.sendall()

    def now_playing(self, item):
        """
        Returns the number of connected scrobblers
        """
        connected = 0
        for scrob in self.scrobblers:
            ret = scrob._now_playing(item)
            debug(item, ret)
            if scrob.next_connexion == -1:
                connected += 1
        return connected


    def put(self, item):
        """
        Adds an element in the queue
        """
        self.log_info(_("Adding %(artist)s - %(title)s ... \n") %
        {'artist':item["artist"], 'title':item["title"]})
        debug(item)
        for scrob in self.scrobblers:
            scrob.queue.append(item)

    def join(self):
        """
        Wait fot threads to stop
        """
        for scrob in self.scrobblers:
            self.log_info(_("Waiting for %s to stop...\n") % scrob)
            if scrob.thread:
                scrob.thread.join()
#}}}


class Reconnection(Thread):#{{{
    """
    Reconnect to a scrobbler
    """
    def __init__(self, scrobbler):
        Thread.__init__(self)
        self.scrobbler = scrobbler

    def run(self):
        self.scrobbler.log_info(_("Trying to reconnect... (%s)\n") %
                self.scrobbler)
        state = False
        try:
            state = self.scrobbler._connect()
        except scrobbler2.ProtocolError, err:
            from re import findall
            a = findall(r"\d+", str(err))
            self.scrobbler.set_connected(False, force=(time.time() +
                int(a[0] if a else 0)))

        if state:
            self.scrobbler.log_info(_("Success !\n"), date=False)
        else:
            self.scrobbler.log_info(_("Fail !\n"), date=False)
#}}}


class ScrobblerConnexion(scrobbler2.Scrobbler): #{{{
    """
    This class manage a scrobbler, the cache associated and the connexion
    failures, followed by a reconnection.
    """
    def __init__(self, config, log_info, log_error, config_glob):
        """
        config is the scrobbler scpecific configuration,
        log_error and log_info are functions
        config_glob is the whole configuration
        """
        self.address = config["address"]
        self.user = config["user"]
        self.password = config["password"]
        self.config = config_glob
        if not self.user and not self.config.fork:
            self.user = raw_input(_("User name for %s : ") % self.address)
        if not self.password and not self.config.fork:
            self.password = md5(getpass(_("Password for %s : ") %
                self.address)).hexdigest()
        self.log_error = log_error
        self.log_info = log_info
        self.reconnect_delay = config_glob.reconnect_delay
        self.queue = list()
        self.flags = {"connecting":False}
        self.thread = None
        scrobbler2.Scrobbler.__init__(self, self.address)

        self.cache_file = os.path.join(config_glob.cache_dir,
                b64encode(self.__repr__()))

        if os.path.exists(self.cache_file):
            debug("opening cache file")
            with open(self.cache_file, "r") as cache_file:
                content = [Song(eval(b64decode(i.strip("\n")))) for i in
                    cache_file.readlines()]
                self.queue.extend(content)
                debug("cache contains : %s" %
                        "\n"+"\n".join(map(str, content)))
            #with open(self.cache_file, "w"): pass # clean

    def _connect(self):
        """
        Connect to a scrobbler and handle errors
        """
        state = False
        # do not reconnect while connecting, through 'sendall' function
        debug("Connecting...")
        self.flags["connecting"] = True
        try:
            state = self.connect(self.user, self.password, client=(CLIENT_ID,
                CLIENT_VERSION))
            if state:
                self.log_info("> "+self.user_url+"\n")
            self.set_connected(state)
        except (scrobbler2.BackendError, scrobbler2.AuthError,
                URLError), err:
            self.set_connected(False)
            self.log_error(_("Unable to connect to %(host)s : %(error)s ") %
                    {'host':self.address, 'error': str(err)})
        if self.next_connexion == -1:
            debug("sendall...")
            self.sendall()
        self.flags["connecting"] = False
        debug("Connection unlocked.", state)
        return state

    def _submit(self, song):
        """
        submit the song and flush the cache
        song is an instance of Song
        Returns :
            0 - if there is an error
            1 - if song has been sent  
            2 - if song has not been sent due to its shortness
        """
        if not self.connection_handler():
            return 0
        position = song["position"]
        if position[1] > 30 and (position[2] >= 50 or position[0] >= 240):
            self.submit(rating="L", **song.format_submit())
            sent = False
            n = False
            essais = 1
            while not sent and essais <= 3:
                try:
                    n = self.flush()
                    sent = True
                except (scrobbler2.BackendError, BadStatusLine) as err:
                    self.log_error(_("Connexion issues (%(error)s) "
                        "%(try)d") % {'error':err, 'try':essais})
                    essais += 1
                except URLError as err:
                    self.log_error(_("URL Error : %s") % unicode(str(err),
                        'utf-8'))
                    break
            if sent:
                self.log_info(_("Submited (%s).\n") % self, date=False)
            else:
                self.log_error(_("Fail (%s) !\n") % self, date=False)
            self.set_connected(n)
            return int(n)
        else:
            return 2
        
    def _now_playing(self, song):
        """
        Send a 'now playing' to the scrobbler
        Returns True on success, False on failure
        """
        if not self.connection_handler():
            return False
        try: # TODO séparer excepts
            if song["artist"] and song["title"]:
                n = scrobbler2.Scrobbler.now_playing(self,
                        **song.format_now_playing())
            else:
                return False
        except (scrobbler2.BackendError, scrobbler2.PostError,
                scrobbler2.SessionError, URLError) as err:
            self.set_connected(False)
            self.log_error(_("Unable to submit the start or the song "
                             "'%(artist)s - %(title)s' : %(error)s") %
                             {'artist':song["artist"], 'title':song["title"],
                              'error': unicode(str(err), 'utf-8')})
            return False
        self.set_connected(n)
        return n

    def connection_handler(self):
        """
        Checks if self is connected, else reconnect
        returns False if it has failed in reconnecting
        """
        debug("Time until reconnexion : %s" % (time.time() - self.next_connexion))
        if self.next_connexion > 0:
            if (not self.flags["connecting"] and self.next_connexion <
                    time.time()):
                self.thread = Reconnection(self)
                debug("thread", self.thread)
                self.thread.start()
            return False
        return True

    def send(self):
        """
        Sends the first element in the queue
        returns the same as in self._submit
        """
        if not self.queue:
            return
        debug("something in queue...")
        item = self.queue.pop(0)
        ret = self._submit(item)
        debug("submitted : ", ret > 0)
        if not ret:
            self.queue.insert(0, item)

        debug("Writing in cache file... ", self.queue)
        with open(self.cache_file, "w") as cache_file:
            cache_file.writelines(b64encode(str(i))+"\n" for i in self.queue)
        
        return ret

    def sendall(self):
        """
        Try to send everything in the queue
        If fail, returns False
        """
        debug("queue", self.queue)
        while self.queue:
            elem = self.queue[0]
            debug("elem", elem)
            self.log_info(_("Submiting : %(artist)s - %(title)s\n") %
                    {"artist":elem["artist"], "title":elem["title"]})
            if not self.send():
                debug("Not sent")
                return False
        return True

    def set_connected(self, connected, force=0):
        """
        Sets either next_connexion to -1 if it's connected, or
        The duration until next connexion. It's value can be forced.
        """
        if connected:
            self.next_connexion = -1
        else:
            self.next_connexion = (time.time() + self.reconnect_delay * 60 if 
                    not force else force)
# }}}


class Song(dict): # {{{
    def __init__(self, content={}):
        dict.__init__(self, content)
        for k in ("start_time", "mbid", "artist", "title",
                "album", "file"):
            if not content.has_key(k):
                self[k] = ""
        if not content.has_key("position"):
            self["position"] = (0, 0, 0)

    def format_now_playing(self):
        return {"artist": self["artist"],
                "track" : self["title"],
                "album" : self["album"],
                "length": self["position"][1],
                "mbid"  : self["mbid"]}
    
    def format_submit(self):
        return {"artist": self["artist"],
                "track" : self["title"],
                "time"  : self["start_time"],
                "length": self["position"][1],
                "album" : self["album"],
                "mbid"  : self["mbid"]}

    def summary(self):
        return (self["artist"], self["title"])

    def set_mbid(self, log_fun=None):
        self["mbid"] = retrieve_mb_id(self["artist"], self["title"])
        if self["mbid"] and log_fun:
            log_fun("> http://musicbrainz.org/track/%s\n" % self["mbid"])

    def set_start_time(self, position):
        self["position"] = position
        self["start_time"] = int(time.time()) - position[0]
#}}}


class Path(object):#{{{
    def __init__(self, value=None, is_file=True, mode="w"):
        """
        Mode can be "w" : read/write or "r" : read only
        """
        self.is_file = is_file
        self.mode = mode
        self.value = value

    def __get__(self, obj, objtype):
        return self.value

    def __set__(self, obj, value):
        clean = (lambda f: os.path.expanduser(os.path.normpath(f)))
        if not value:
            self.value = value
            return
        value = clean(value)
        self.value = value
        dirname = os.path.dirname(value) if self.is_file else value
        self.mkdir_cond(dirname)
        if not self.is_file:
            return
        if os.path.exists(value):
            mode = "r+" if self.mode is "w" else "r"
            try:
                with open(value, mode): pass
            except IOError:
                self.error(_("Cannot %(mode)s file %(file)s.") %
                        {"mode":("read" if mode is "r" else "write"),
                         "file":value})
        elif self.mode is "r":
            self.error(_("File %s doesn't exist") % value)
        else:
            try:
                with open(value, "w"): pass
            except IOError:
                self.error(_("Cannot write in file %s.") % value)

    def error(self, err):
        print >> sys.stderr, err

    def mkdir_cond(self, directory):
        if not os.path.exists(directory):
            try:
                os.makedirs(directory)
            except OSError:
                self.error(_("Cannot create directory %s.") % directory)
#}}}


class Config(dict): #{{{
    error_file  = Path()
    info_file   = Path()
    pid_file    = Path()
    cache_dir   = Path(is_file=False)
    config_file = Path(mode="r")

    def __init__(self, argv=sys.argv):
        self.argv = argv
        self.mpd_port = 6600
        self.mpd_host = "localhost"
        self.mpd_password = ""
        self.fork = False
        self.error_file = os.getcwd()+"/errors.log"
        self.info_file = os.getcwd()+"/infos.log"
        self.pid_file = os.getcwd()+"/scrobd.pid"
        self.cache_dir = os.getcwd()+"/cache/"
        self.reconnect_delay = 5
        self.refresh_delay = 5
        self.config_file = os.path.expanduser("~/.scrobdrc")
        self.scrobblers = []
        self.verbose = False
        self.replaces = {'curdir':os.getcwd()}

    def __parse_file(self):
        """
        Configuration syntax :
        set opt1 arg
        set opt2 "ar g"
        set fork on
        # Options available : the vars defined in __init__
        scrobbler address "addr1" user "foo" password "bar"
        scrobbler address "addr2" user "baz" password "qux"
        """
        def _split(line):
            elems = []
            elem = ""
            nosplit = False
            for c in line:
                if c is " " and not nosplit:
                    elems.append(elem)
                    elem = ""
                elif c in ('"', "'"):
                    nosplit = not nosplit
                else:
                    elem += c
            elems.append(elem)
            return elems

        def error(line, err):
            print >>sys.stderr, (_("%(file)s : syntax error line %(line)d : "
                "%(err)s") % {'file':self.config_file, 'line':line, 'err':err})
        
        try:
            with open(self.config_file, 'r') as config_file:
                for i, line in enumerate(config_file):
                    line = line.strip('\n').lstrip()
                    for key in self.replaces:
                        line = line.replace("%%[%s]" % key, self.replaces[key])
                    if line.startswith('set'):
                        sline = _split(line)
                        if len(sline) < 2:
                            error(i+1, _("need moar arguments."))
                            return 1
                        opt = sline[1]
                        arg = sline[2] if len(sline) > 2 else ""
                        if self.verbose:
                            print opt+"="+arg+";",
                        if opt in self.__dict__ or (opt in dir(self)
                                and (opt.find("file") != -1
                                or opt.find("dir") != -1)):
                                # TODO read some docs about dir() and __dict__
                            defval = getattr(self, opt)
                            if self.verbose:
                                print "def: %s (%s)" % (defval, type(defval))
                            if isinstance(defval, bool):
                                if arg.lower() in ("no", "false", "off"):
                                    setattr(self, opt, False)
                                else: # The argument can be empty
                                    setattr(self, opt, True)
                            elif isinstance(defval, (int, str, unicode, float)):
                                try:
                                    setattr(self, opt, type(defval)(arg))
                                except ValueError:
                                    error(i+1, _("value error."))
                                    return 1
                            else:
                                error(i+1, _("what the hell did you do ???"))
                                return 1
                        else:
                            error(i+1, _("option not reconized"))
                            return 1
                    elif line.startswith("scrobbler"):
                        sline = _split(line)
                        scrob = {}
                        checksum = 0
                        elems = {"address":1, "user":10, "password":100}
                        pass_next = False
                        for n, opt in enumerate(sline[1:]):
                            if pass_next:
                                pass_next = False
                                continue
                            for key in elems:
                                if opt == key:
                                    checksum += elems[key]
                                    try:
                                        arg = sline[n+2]
                                        pass_next = True
                                    except IndexError:
                                        error(i+1, _("%s must have an "
                                        "argument.") % opt)
                                        return 1
                                    scrob[key] = arg
                        if checksum != 111:
                            error(i+1, _("wrong number of arguments."))
                            return 1
                        self.scrobblers.append(scrob)
                    # otherwise, ignore.
        except IOError:
            print _("Unable to open config file %s.") % self.config_file
            return 1

    def make_config(self):
        usage = ("Usage : %s [-p port] [-s host] [-i info file] "
                 "[-e error file] [-d] [-h] [-k] [--no-error-file]"
                 " [--no-info-file] [config_file]\n"
                 " -p,--port        the MPD port\n"
                 " -s,--host        the MPD address\n"
                 " -i,--info-file   the log file for the informations\n"
                 " -e,--error-file  the log file for the errors\n"
                 " -d,--daemon      run as daemon\n"
                 " -h,--help        view this help\n"
                 " -k,--kill        kill another instance of %s\n"
                 " -v               verbose mode (for debugging, do not use)\n"
                 " --no-error-file  print errors on stderr\n"
                 " --no-info-file   print infos on strdout\n"
                 " config_file      the path to the config file. If nothing\n"
                 "                  is specified, use %s" %
                 (self.argv[0], self.argv[0], self.config_file))
        try:
            opts, args = getopt(self.argv[1:], 'p:s:w:u:e:i:dhkv',
                ('port=', 'host=', 'password=', 'username=', 'daemon', 'help',
                'error-file=', 'info-file=', 'kill', 'no-error-file',
                'no-info-file'))
        except GetoptError as err:
            print >> sys.stderr, _("Unknown argument : %s.") % err.opt
            print usage
            sys.exit(1)
        if ('-v', '') in opts:
            self.verbose = True
        if args:
            self.config_file = args[0]
        if self.__parse_file():
            sys.exit(1)
        for o, a in opts:
            if o in ('-p', '--port'):
                self.mpd_port = a
            elif o in ('-s', '--host'):
                self.mpd_host = a
            elif o in ('-e', '--error-file'):
                self.error_file = a
            elif o in ('-i', '--info-file'):
                self.info_file = a
            elif o in ('--no-error-file'):
                self.error_file = None
            elif o in ('--no-info-file'):
                self.info_file = None
            elif o in ('-d', '--daemon'):
                self.fork = True
            elif o in ('-k', '--kill'):
                # TODO'kill' function
                try:
                    with open(self.pid_file, "r") as pidf:
                        pid = int(pidf.read())
                        os.kill(pid, 15)
                    os.remove(self.pid_file)
                except IOError:
                    print >> sys.stderr, (_("No pid file. Kill manually, with the"
                            " 'kill' command"))
                except OSError:
                    print >> sys.stderr, _("Unable to kill %d.") % pid
                    try:
                        os.remove(self.pid_file)
                    except:
                        pass
                sys.exit(0)
            elif o in ('-h', '--help'):
                print usage
                sys.exit(0)

#}}}

# All the encoding stuff has been stolen from here :
# http://hachoir.org/browser/trunk/hachoir-core/hachoir_core/i18n.py#L23
# and http://haypo.hachoir.org/trac/browser/misc/unicode2ascii.py
# Thanks to haypo, the master.

# Encoding, locale {{{
def unicode2ascii(s):
    if not s:
        return s
    u2a = {u"Æ": u"AE",
            u"æ": u"ae",
            u"Ø": u"O",
            u"ø": u"o",
            u"Œ": u"OE",
            u"œ": u"oe",
            u"ł": u"l",
            u"ß": u"ss",

            # Various signs
            u"¡": u"!",
            u"©": u"(c)",
            u"«": u'"',
            u"®": u"(r)",
            u"²": u"2",
            u"»": u'"',
            u"⁄": u"/",

            # Greek
            u"Α": u"A",
            u"Β": u"B",
            u"Ε": u"E",
            u"Ζ": u"Z",
            u"Η": u"H",
            u"Θ": u"O",
            u"Ι": u"I",
            u"Κ": u"K",
            u"Μ": u"M",
            u"Ν": u"N",
            u"Ο": u"O",
            u"Ρ": u"P",
            u"Τ": u"T",
            u"Υ": u"Y",
            u"Χ": u"X",
            u"α": u"a",
            u"β": u"b",
            u"γ": u"y",
            u"ε": u"e",
            u"η": u"n",
            u"ο": u"o",
            u"ρ": u"p",
            u"υ": u"v",

            # Cyrillic
            u"І": u"I",
            u"Ј": u"J",
            u"В": u"B",
            u"Е": u"E",
            u"И": u"N",
            u"З": u"3",
            u"К": u"K",
            u"М": u"M",
            u"Н": u"H",
            u"О": u"O",
            u"Р": u"P",
            u"С": u"C",
            u"Т": u"T",
            u"У": u"Y",
            u"Х": u"X",
            u"Я": u"R",
            u"а": u"a",
            u"в": u"b",
            u"е": u"e",
            u"з": u"3",
            u"к": u"k",
            u"м": u"m",
            u"н": u"h",
            u"о": u"o",
            u"р": u"p",
            u"с": u"c",
            u"т": u"T",
            u"у": u"y",
            u"х": u"x",
            u"я": u"R",
            u"і": u"i",
            u"ј": u"j",
        }
    s = unicode(s, "utf8")
    out = ""
    for c in s:
        c = normalize('NFKD', c)[0]
        if c in u2a:
            out += u2a[c]
        if ord(c) <= 127:
            out += c
    return out.encode("ascii", "strict")
 
def _get_terminal_charset():
    """
    Function used by get_terminal_charset() to get terminal charset.

    @see get_terminal_charset()
    """
    # (1) Try locale.getpreferredencoding()
    try:
        charset = locale.getpreferredencoding()
        if charset:
            return charset
    except (locale.Error, AttributeError):
        pass

    # (2) Try locale.nl_langinfo(CODESET)
    try:
        charset = locale.nl_langinfo(locale.CODESET)
        if charset:
            return charset
    except (locale.Error, AttributeError):
        pass

    # (3) Try sys.stdout.encoding
    if hasattr(sys.stdout, "encoding") and sys.stdout.encoding:
        return sys.stdout.encoding

    # (4) Otherwise, returns "ASCII"
    return "ASCII"

def get_terminal_charset():
    """
    Guess terminal charset using differents tests:
    1. Try locale.getpreferredencoding()
    2. Try locale.nl_langinfo(CODESET)
    3. Try sys.stdout.encoding
    4. Otherwise, returns "ASCII"

    WARNING: Call init_locale() before calling this function.
    """
    try:
        return get_terminal_charset.value
    except AttributeError:
        get_terminal_charset.value = _get_terminal_charset()
        return get_terminal_charset.value

class UnicodeStdout:
    def __init__(self, old_device, charset):
        self.device = old_device
        self.charset = charset

    def flush(self):
        self.device.flush()

    def write(self, text):
        if isinstance(text, unicode):
            text = text.encode(self.charset, 'replace')
        self.device.write(text)
        self.device.flush()

def init_locale():
    # Only initialize locale once
    try:
        if init_locale.is_done:
            return
    except AttributeError:
        pass
    init_locale.is_done = True

    # Setup locales
    try:
        locale.setlocale(locale.LC_ALL, "")
    except (locale.Error, IOError):
        pass

    # Get the terminal charset
    charset = get_terminal_charset()

    # Replace stdout and stderr by unicode objet supporting unicode string
    sys.stdout = UnicodeStdout(sys.stdout, charset)
    sys.stderr = UnicodeStdout(sys.stderr, charset)
    return charset

def _dummy_gettext(text):
    return unicode(text)

def _dummy_ngettext(singular, plural, count):
    if 1 < abs(count) or not count:
        return unicode(plural)
    else:
        return unicode(singular)

def _init_gettext():
    charset = init_locale()

    # Try to load gettext module
    try:
        import gettext
    except ImportError:
        return (_dummy_gettext, _dummy_ngettext)

    # Gettext variables
    app = "scrobd"
    locale_dir = "locale"

    # Initialize gettext module
    gettext.bindtextdomain(app, locale_dir)
    gettext.textdomain(app)
    translate = gettext.gettext
    ngettext = gettext.ngettext

    # TODO: translate_unicode lambda function really sucks!
    # => find native function to do that
    unicode_gettext = lambda text: \
        unicode(translate(text), charset)
    unicode_ngettext = lambda singular, plural, count: \
        unicode(ngettext(singular, plural, count), charset)
    return (unicode_gettext, unicode_ngettext)

UTF_BOMS = (
    (codecs.BOM_UTF8, "UTF-8"),
    (codecs.BOM_UTF16_LE, "UTF-16-LE"),
    (codecs.BOM_UTF16_BE, "UTF-16-BE"),
)

# Set of valid characters for specific charset
CHARSET_CHARACTERS = (
    # U+00E0: LATIN SMALL LETTER A WITH GRAVE
    (set(u"©®éêè\xE0ç".encode("ISO-8859-1")), "ISO-8859-1"),
    (set(u"©®éêè\xE0ç€".encode("ISO-8859-15")), "ISO-8859-15"),
    (set(u"©®".encode("MacRoman")), "MacRoman"),
    (set(u"εδηιθκμοΡσςυΈί".encode("ISO-8859-7")), "ISO-8859-7"),
)

def guessBytesCharset(bytes, default=None):
    r"""
    >>> guessBytesCharset("abc")
    'ASCII'
    >>> guessBytesCharset("\xEF\xBB\xBFabc")
    'UTF-8'
    >>> guessBytesCharset("abc\xC3\xA9")
    'UTF-8'
    >>> guessBytesCharset("File written by Adobe Photoshop\xA8 4.0\0")
    'MacRoman'
    >>> guessBytesCharset("\xE9l\xE9phant")
    'ISO-8859-1'
    >>> guessBytesCharset("100 \xA4")
    'ISO-8859-15'
    >>> guessBytesCharset('Word \xb8\xea\xe4\xef\xf3\xe7 - Microsoft Outlook 97 - \xd1\xf5\xe8\xec\xdf\xf3\xe5\xe9\xf2 e-mail')
    'ISO-8859-7'
    """
    # Check for UTF BOM
    for bom_bytes, charset in UTF_BOMS:
        if bytes.startswith(bom_bytes):
            return charset

    # Pure ASCII?
    try:
        text = unicode(bytes, 'ASCII', 'strict')
        return 'ASCII'
    except UnicodeDecodeError:
        pass

    # Valid UTF-8?
    try:
        text = unicode(bytes, 'UTF-8', 'strict')
        return 'UTF-8'
    except UnicodeDecodeError:
        pass

    # Create a set of non-ASCII characters
    non_ascii_set = set( byte for byte in bytes if ord(byte) >= 128 )
    for characters, charset in CHARSET_CHARACTERS:
        if characters.issuperset(non_ascii_set):
            return charset
    return default

gettext, ngettext = _init_gettext()
_ = gettext
#}}}

# Some functions {{{
def report_error(error, error_file=None, date=True):
    text = ("     %s\n" % error if not date else
            time.strftime("%d/%m/%y %T ")+error+"\n")
    if not error_file:
        sys.stderr.write(text)
        sys.stderr.flush()
    else:
        with codecs.open(error_file, "a", "utf-8") as ef:
            ef.write(text)

def info(info, report_file=None, date=True):
    text = ("    %s" % info if not date else
            time.strftime("%d/%m/%y %T ")+info)
    if not report_file:
        sys.stdout.write(text)
        sys.stdout.flush()
    else:
        with codecs.open(report_file, "a", "utf-8") as rf:
            rf.write(text)

def debug(*args):
    """
    Print args if DEBUG["context"] is True
    """
    try:
        frame = inspect.stack()[1][0]
        context = inspect.getframeinfo(frame)
        locals_ = inspect.getargvalues(frame)[3]
        name = context[2]
        if "self" in locals_:
            name = "%s.%s" % (str(locals_["self"].__class__).split(".")[-1], name)
    except IndexError:
        name = ""
    if DEBUG.setdefault(name, False):
        print >> sys.stderr, "[%s] %s" % (name, " ".join(map(str, args)))

def retrieve_mb_id(artist, title):
    return
    if not artist and not title:
        return ""
    if musicbrainz:
        try:
            q = musicbrainz.Query()
            f = musicbrainz.TrackFilter(title=title, artistName=artist)
            result = q.getTracks(f)
            mbid = result[0].track.id
        except IndexError:
            return ""
        except Exception, err:
            debug(err)
            return ""
        return mbid.split('/')[-1]
    else:
        return ""
#}}}

def main(): #{{{
    conf = Config()
    # Parse command args and config file
    conf.make_config()
    #conf.check_files()
    log_info = (lambda text, date=True: info(text, conf.info_file, date))
    log_error = (lambda text, date=True: report_error(text, conf.error_file,
        date))

    # Connecting to MPD
    if not conf.mpd_host and not conf.fork:
        conf.mpd_host = raw_input(_("The MPD server address (default: "
            "127.0.0.1): "))
    if not conf.mpd_port and not conf.fork:
        conf.mpd_port = -1
        while conf.mpd_port < 0:
            try:
                conf.mpd_port = int(raw_input(_("The MPD sever port (default"
                ": 6600)")))
            except ValueError:
                pass
    try:
        mpdc = MPDInfos(conf.mpd_host, conf.mpd_port, conf.mpd_password,
                log_info, log_error)
        mpdc.connect()
    except mpd.MPDError, err:
        # TODO add this to MPDInfos
        log_error(_("Unable to connect to %(host)s:%(port)s : %(error)s") %
            {'host':conf.mpd_host, 'port':conf.mpd_port, 'error':err})
        log_info(_("Trying to reconnect... \n"))
        ret = mpdc.reconnect()
        if ret:
            log_error(_("FAIL : %s") % ret)
            sys.exit(1)

    # Connecting to scrobblers
    scrobblers = []
    log_info(_("Connecting to scrobblers...\n"))
    for scrob_conf in conf.scrobblers:
        scrobbler = ScrobblerConnexion(scrob_conf, log_info,
            log_error, conf)
        scrobbler._connect()
        scrobblers.append(scrobbler)

    scrobblers_queue = ScrobblersQueue(scrobblers, conf, log_info, log_error)
    if not scrobblers_queue.connected():
        log_error(_("Not connected to any scrobbler."))
        #return 2

    # Daemonize
    pid = None
    if conf.fork:
        pid = os.fork()
    if pid:
        sys.exit(0)
    else:
        with open(conf.pid_file, "w") as pidf:
            pidf.write(str(os.getpid()))

    prev_song = Song()
    cur_position = old_position = (0, 0, 0)
    try:
        while True:
            cur_song = mpdc.get_song()
            # Si le fichier est différent du précédent, ou si la différence 
            # entre le temps actuel et le temps précédent est négative (oui,
            # c'est tordu), alors la chanson a changé.
            time_diff = cur_position[0] - old_position[0]
            if cur_song['file'] != prev_song['file'] or time_diff < 0:
                # On envoie prev_song (on le place dans scrobblers_queue) et on
                # soumet le démarrage de cur_song
                title_disp = (lambda a, t: "%s - %s" % (a, t) if a and t else
                        os.path.basename(cur_song["file"]))(*cur_song.summary())
                log_info(_("Song changed : %s\n") % title_disp)
                cur_song.set_mbid(log_info)
                if time_diff < 0:
                    cur_position = old_position
                # Submit
                prev_song.set_start_time(cur_position)
                artist, title = prev_song.summary()

                if title and artist:
                    scrobblers_queue.put(prev_song)
                    scrobblers_queue.send()

                prev_song = cur_song
                # Now Playing
                cur_song.set_start_time(mpdc.get_time())
                scrobblers_queue.now_playing(cur_song)
                cur_position = (0, 0, 0)

            old_position = cur_position
            cur_position = mpdc.get_time()

            scrobblers_queue.send()
            
            time.sleep(conf.refresh_delay)
    finally:
        log_info("Ending...\n")
        scrobblers_queue.join()
        try:
            os.remove(conf.pid_file)
        except:
            pass

#}}}

if __name__ == "__main__":
    try:
        rc = main()
    except (KeyboardInterrupt, SystemExit):
        rc = 1
    sys.exit(rc)

