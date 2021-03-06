#!/usr/bin/python
# encoding: utf-8
#
# Copyright 2009-2017 Erik Gomez.
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# InstallApplications
# This script uses munki's gurl.py to download the initial json and
# subsequent packages securely, and then install them. This allows your DEP
# bootstrap to be completely dynamic and easily updateable.
# downloadfile function taken from:
# https://gist.github.com/gregneagle/1816b650df8e3fbeb18f
# gurl.py and gethash function taken from:
# https://github.com/munki/munki
# Notice a pattern?

from distutils.version import LooseVersion
from Foundation import NSLog
from SystemConfiguration import SCDynamicStoreCopyConsoleUser
import hashlib
import json
import optparse
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
sys.path.append('/usr/local/installapplications')
# PEP8 can really be annoying at times.
import gurl  # noqa


g_dry_run = False


def deplog(text):
    depnotify = '/private/var/tmp/depnotify.log'
    with open(depnotify, 'a+') as log:
        log.write(text + '\n')


def iaslog(text):
    NSLog('[InstallApplications] ' + text)


def getconsoleuser():
    cfuser = SCDynamicStoreCopyConsoleUser(None, None, None)
    return cfuser


def pkgregex(pkgpath):
    try:
        # capture everything after last / in the pkg filepath
        pkgname = re.compile(r"[^/]+$").search(pkgpath).group(0)
        return pkgname
    except AttributeError, IndexError:
        return packagepath


def installpackage(packagepath):
    try:
        cmd = ['/usr/sbin/installer', '-verboseR', '-pkg', packagepath,
               '-target', '/']
        if g_dry_run:
            iaslog('Dry run installing package: %s' % packagepath)
            return 0
        proc = subprocess.Popen(cmd, shell=False, bufsize=-1,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        output, rcode = proc.communicate(), proc.returncode
        installlog = output[0].split('\n')
        # Filter all blank lines after the split.
        for line in filter(None, installlog):
            # Replace any instances of % with a space and any elipsis with
            # a blank line since NSLog can't handle these kinds of characters.
            # Hopefully this is the only bad characters we will ever run into.
            logline = line.replace('%', ' ').replace('\xe2\x80\xa6', '')
            iaslog(logline)
        return rcode
    except Exception:
        pass


def checkreceipt(packageid):
    try:
        cmd = ['/usr/sbin/pkgutil', '--pkg-info-plist', packageid]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = proc.communicate()
        receiptout = output[0]
        if receiptout:
            plist = plistlib.readPlistFromString(receiptout)
            version = plist['pkg-version']
        else:
            version = '0.0.0.0.0'
        return version
    except Exception:
        version = '0.0.0.0.0'
        return version


def gethash(filename):
    hash_function = hashlib.sha256()
    if not os.path.isfile(filename):
        return 'NOT A FILE'

    fileref = open(filename, 'rb')
    while 1:
        chunk = fileref.read(2**16)
        if not chunk:
            break
        hash_function.update(chunk)
    fileref.close()
    return hash_function.hexdigest()


def launchctl(*arg):
    # Use *arg to pass unlimited variables to command.
    cmd = arg
    run = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, err = run.communicate()
    return output


def downloadfile(options):
    connection = gurl.Gurl.alloc().initWithOptions_(options)
    percent_complete = -1
    bytes_received = 0
    connection.start()
    try:
        filename = options['name']
    except KeyError:
        iaslog('No \'name\' key defined in json for %s' %
               pkgregex(options['file']))
        sys.exit(1)

    try:
        while not connection.isDone():
            if connection.destination_path:
                # only print progress info if we are writing to a file
                if connection.percentComplete != -1:
                    if connection.percentComplete != percent_complete:
                        percent_complete = connection.percentComplete
                        iaslog('Downloading %s - Percent complete: %s ' % (
                               filename, percent_complete))
                elif connection.bytesReceived != bytes_received:
                    bytes_received = connection.bytesReceived
                    iaslog('Downloading %s - Bytes received: %s ' % (
                           filename, bytes_received))

    except (KeyboardInterrupt, SystemExit):
        # safely kill the connection then fall through
        connection.cancel()
    except Exception:  # too general, I know
        # Let us out! ... Safely! Unexpectedly quit dialogs are annoying ...
        connection.cancel()
        # Re-raise the error
        raise

    if connection.error is not None:
        iaslog('Error: %s %s ' % (str(connection.error.code()),
                                  str(connection.error.localizedDescription()))
               )
        if connection.SSLerror:
            iaslog('SSL error: %s ' % (str(connection.SSLerror)))
    if connection.response is not None:
        iaslog('Status: %s ' % (str(connection.status)))
        iaslog('Headers: %s ' % (str(connection.headers)))
    if connection.redirection != []:
        iaslog('Redirection: %s ' % (str(connection.redirection)))


def vararg_callback(option, opt_str, value, parser):
    # https://docs.python.org/3/library/optparse.html#callback-example-6-
    # variable-arguments
    assert value is None
    value = []

    def floatable(str):
        try:
            float(str)
            return True
        except ValueError:
            return False

    for arg in parser.rargs:
        # stop on --foo like options
        if arg[:2] == "--" and len(arg) > 2:
            break
        value.append(arg)

    del parser.rargs[:len(value)]
    setattr(parser.values, option.dest, value)


def runrootscript(pathname, donotwait):
    '''Runs script located at given pathname'''
    if g_dry_run:
        iaslog('Dry run executing root script: %s' % pathname)
        return True
    try:
        if donotwait:
            iaslog('Do not wait triggered')
            proc = subprocess.Popen(pathname)
            iaslog('Running Script: %s ' % (str(pathname)))
        else:
            proc = subprocess.Popen(pathname, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            iaslog('Running Script: %s ' % (str(pathname)))
            (out, err) = proc.communicate()
            if err and proc.returncode == 0:
                iaslog('Output from %s on stderr but ran successfully: %s' %
                       (pathname, err))
            elif proc.returncode > 0:
                iaslog('Failure running script: ' + str(err))
                return False
    except OSError as err:
        iaslog('Failure running script: ' + str(err))
        return False
    return True


def runuserscript(iauserscriptpath):
    files = os.listdir(iauserscriptpath)
    for file in files:
        pathname = os.path.join(iauserscriptpath, file)
        if g_dry_run:
            iaslog('Dry run executing user script: %s' % pathname)
            os.remove(pathname)
            return True
        try:
            proc = subprocess.Popen(pathname, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            iaslog('Running Script: %s ' % (str(pathname)))
            (out, err) = proc.communicate()
            if err and proc.returncode == 0:
                iaslog(
                    'Output from %s on stderr but ran successfully: %s' %
                    (pathname, err))
            elif proc.returncode > 0:
                iaslog('Failure running script: ' + str(err))
                return False
        except OSError as err:
            iaslog('Failure running script: ' + str(err))
            return False
        os.remove(pathname)
        return True
    else:
        iaslog('No user scripts found!')
        return False


def download_if_needed(item, stage, type, opts, depnotifystatus):
    # Check if the file exists and matches the expected hash.
    path = item['file']
    name = item['name']
    hash = item['hash']
    while not (os.path.isfile(path) and hash == gethash(path)):
        # Check if additional headers are being passed and add
        # them to the dictionary.
        if opts.headers:
            item.update({'additional_headers':
                         {'Authorization': opts.headers}})
        # Download the file once:
        iaslog('Starting download: %s' % (item['url']))
        if opts.depnotify:
            if stage == 'setupassistant':
                iaslog(
                    'Skipping DEPNotify notification due to \
                    setupassistant.')
            else:
                if depnotifystatus:
                    deplog('Status: Downloading %s' % (name))
        downloadfile(item)
        # Wait half a second to process
        time.sleep(0.5)
        # Check the files hash and redownload until it's
        # correct. Bail after three times and log event.
        failsleft = 3
        while not hash == gethash(path):
            iaslog('Hash failed for %s - received: %s expected\
                   : %s' % (name, gethash(path), hash))
            downloadfile(item)
            failsleft -= 1
            if failsleft == 0:
                iaslog('Hash retry failed for %s: exiting!\
                       ' % name)
                sys.exit(1)
        # Time to install.
        iaslog('Hash validated - received: %s expected: %s' % (
               gethash(path), hash))
        # Fix script permissions.
        if os.path.splitext(path)[1] != ".pkg":
            os.chmod(path, 0755)
        if type is 'userscript':
            os.chmod(path, 0777)


def touch(path):
    try:
        touchfile = ['/usr/bin/touch', path]
        proc = subprocess.Popen(touchfile, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        touchfileoutput, err = proc.communicate()
        os.chmod(path, 0777)
        return touchfileoutput
    except Exception:
        return None


def main():
    # Options
    usage = '%prog [options]'
    o = optparse.OptionParser(usage=usage)
    o.add_option('--depnotify', default=None,
                 dest="depnotify",
                 action="callback",
                 callback=vararg_callback,
                 help=('Optional: Utilize DEPNotify and pass options to it.'))
    o.add_option('--headers', help=('Optional: Auth headers'))
    o.add_option('--jsonurl', help=('Required: URL to json file.'))
    o.add_option('--iapath',
                 default='/Library/Application Support/installapplications',
                 help=('Optional: Specify InstallApplications package path.'))
    o.add_option('--ldidentifier',
                 default='com.erikng.installapplications',
                 help=('Optional: Specify LaunchDaemon identifier.'))
    o.add_option('--laidentifier',
                 default='com.erikng.installapplications',
                 help=('Optional: Specify LaunchAgent identifier.'))
    o.add_option('--reboot', default=None,
                 help=('Optional: Trigger a reboot.'), action='store_true')
    o.add_option('--dry-run', help=('Optional: Dry run (for testing).'),
                 action='store_true')
    o.add_option('--userscript', default=None,
                 help=('Optional: Trigger a user script run.'),
                 action='store_true')

    opts, args = o.parse_args()

    # Dry run that doesn't actually run or install anything.
    if opts.dry_run:
        global g_dry_run
        g_dry_run = True

    # Begin logging events
    iaslog('Beginning InstallApplications run')

    # installapplications variables
    iapath = opts.iapath
    iauserscriptpath = os.path.join(iapath, 'userscripts')
    iatmppath = '/var/tmp/installapplications'
    iaslog('InstallApplications path: ' + str(iapath))
    ldidentifierplist = opts.ldidentifier + '.plist'
    ialdpath = os.path.join('/Library/LaunchDaemons', ldidentifierplist)
    iaslog('InstallApplications LaunchDaemon path: ' + str(ialdpath))
    laidentifierplist = opts.laidentifier + '.plist'
    ialapath = os.path.join('/Library/LaunchAgents', laidentifierplist)
    iaslog('InstallApplications LaunchAgent path: ' + str(ialapath))
    depnotifystatus = True

    # Ensure the directories exist
    if not os.path.isdir(iauserscriptpath):
        for path in [iauserscriptpath, iatmppath]:
            if not os.path.isdir(path):
                os.makedirs(path)
                os.chmod(path, 0777)

    # hardcoded json fileurl path
    jsonpath = os.path.join(iapath, 'bootstrap.json')
    iaslog('InstallApplications json path: ' + str(jsonpath))

    # User script touch path
    userscripttouchpath = '/var/tmp/installapplications/.userscript'

    if opts.userscript:
        iaslog('Running in userscript mode')
        uscript = runuserscript(iauserscriptpath)
        if uscript:
            os.remove(userscripttouchpath)
            sys.exit(0)
        else:
            iaslog('Failed to run script!')
            sys.exit(1)

    # DEPNotify trigger commands that need to happen at the end of a run
    deptriggers = ['Command: Quit', 'Command: Restart', 'Command: Logout',
                   'DEPNotifyPath', 'DEPNotifyArguments',
                   'DEPNotifySkipStatus']

    # Look for all the DEPNotify options but skip the ones that are usually
    # done after a full run.
    if opts.depnotify:
        for varg in opts.depnotify:
            notification = str(varg)
            if any(x in notification for x in deptriggers):
                if 'DEPNotifySkipStatus' in notification:
                    depnotifystatus = False
            else:
                iaslog('Sending %s to DEPNotify' % (str(notification)))
                deplog(notification)

    # Check for root and json url.
    if opts.jsonurl:
        jsonurl = opts.jsonurl
        if not g_dry_run and (os.getuid() != 0):
            print 'InstallApplications requires root!'
            sys.exit(1)
    else:
        iaslog('No JSON URL specified!')
        sys.exit(1)

    # Make the temporary folder
    try:
        os.makedirs(iapath)
    except Exception:
        pass

    # json data for gurl download
    json_data = {
            'url': jsonurl,
            'file': jsonpath,
            'name': 'Bootstrap.json'
        }
    
    # Grab auth headers if they exist and update the json_data dict.
    if opts.headers:
        headers = {'Authorization': opts.headers}
        json_data.update({'additional_headers': headers})

    # If the file doesn't exist, grab it and wait half a second to save.
    while not os.path.isfile(jsonpath):
        iaslog('Starting download: %s' % (json_data['url']))
        downloadfile(json_data)
        time.sleep(0.5)

    # Load up file to grab all the items.
    iajson = json.loads(open(jsonpath).read())

    # Set the stages
    stages = ['setupassistant', 'userland']

    # Get the number of items for DEPNotify
    if opts.depnotify:
        numberofitems = 0
        for stage in stages:
            if stage == 'setupassistant':
                iaslog('Skipping DEPNotify item count due to setupassistant.')
            else:
                numberofitems += int(len(iajson[stage]))
        # Mulitply by two for download and installation status messages
        if depnotifystatus:
            deplog('Command: Determinate: %d' % (numberofitems*2))

    # Process all stages
    for stage in stages:
        iaslog('Beginning %s' % (stage))
        if stage == 'userland':
            # Open DEPNotify for the admin if they pass
            # condition.
            depnotifypath = None
            depnotifyarguments = None
            if opts.depnotify:
                for varg in opts.depnotify:
                    depnstr = str(varg)
                    if 'DEPNotifyPath:' in depnstr:
                        depnotifypath = depnstr.split(' ', 1)[-1]
                    if 'DEPNotifyArguments:' in depnstr:
                        depnotifyarguments = depnstr.split(' ', 1)[-1]
            if depnotifypath:
                while (getconsoleuser()[0] is None
                       or getconsoleuser()[0] == u'loginwindow'
                       or getconsoleuser()[0] == u'_mbsetupuser'):
                    iaslog('Detected SetupAssistant in userland stage - \
                           delaying DEPNotify launch until user session.')
                    time.sleep(1)
                iaslog('Creating DEPNotify Launcher')
                depnotifyscriptpath = os.path.join(
                    iauserscriptpath,
                    'depnotifylauncher.py')
                if depnotifyarguments:
                    if '-munki' in depnotifyarguments:
                        # Touch Munki Logs if they do not exist so DEPNotify
                        # can show them.
                        mlogpath = '/Library/Managed Installs/Logs'
                        mlogfile = os.path.join(mlogpath,
                                                'ManagedSoftwareUpdate.log')
                        if not os.path.isdir(mlogpath):
                            os.makedirs(mlogpath, 0755)
                        if not os.path.isfile(mlogfile):
                            touch(mlogfile)
                    depnotifystring = 'depnotifycmd = ' \
                        """['/usr/bin/open', '""" + depnotifypath + "', '" + \
                        '--args' + """', '""" + depnotifyarguments + "']"
                else:
                    depnotifystring = 'depnotifycmd = ' \
                        """['/usr/bin/open', '""" + depnotifypath + "']"
                depnotifyscript = "#!/usr/bin/python"
                depnotifyscript += '\n' + "import subprocess"
                depnotifyscript += '\n' + depnotifystring
                depnotifyscript += '\n' + 'subprocess.call(depnotifycmd)'
                with open(depnotifyscriptpath, 'wb') as f:
                    f.write(depnotifyscript)
                os.chmod(depnotifyscriptpath, 0777)
                touch(userscripttouchpath)
                while os.path.isfile(userscripttouchpath):
                    iaslog('Waiting for DEPNotify script to complete')
                    time.sleep(0.5)
        # Loop through the items and download/install/run them.
        for item in iajson[stage]:
            # Set the filepath, name and type.
            try:
                path = item['file']
                name = item['name']
                type = item['type']
            except KeyError as e:
                iaslog('Invalid item %s: %s' % (repr(item), str(e)))
                continue
            iaslog('%s processing %s %s at %s' % (stage, type, name, path))

            if type == 'package':
                packageid = item['packageid']
                version = item['version']
                # Compare version of package with installed version
                if LooseVersion(checkreceipt(packageid)) >= LooseVersion(
                        version):
                    iaslog('Skipping %s - already installed.' % (name))
                else:
                    # Download the package if it isn't already on disk.
                    download_if_needed(item, stage, type, opts,
                                       depnotifystatus)

                    # On userland stage, we want to wait until we are actually
                    # in the user's session.
                    if stage == 'userland':
                        if len(iajson['userland']) > 0:
                            while (getconsoleuser()[0] is None
                                   or getconsoleuser()[0] == u'loginwindow'
                                   or getconsoleuser()[0] == u'_mbsetupuser'):
                                iaslog('Detected SetupAssistant in userland \
                                       stage - delaying install until user \
                                       session.')
                                time.sleep(1)
                    iaslog('Installing %s from %s' % (name, path))
                    if opts.depnotify:
                        if stage == 'setupassistant':
                            iaslog(
                                'Skipping DEPNotify notification due to \
                                setupassistant.')
                        else:
                            if depnotifystatus:
                                deplog('Status: Installing: %s' % (name))
                    # Install the package
                    installerstatus = installpackage(item['file'])
            elif type == 'rootscript':
                if 'url' in item:
                    download_if_needed(item, stage, type, opts,
                                       depnotifystatus)
                iaslog('Starting root script: %s' % (path))
                try:
                    donotwait = item['donotwait']
                except KeyError as e:
                    donotwait = False
                if opts.depnotify:
                    if depnotifystatus:
                        deplog('Status: Installing: %s' % (name))
                if donotwait:
                    runrootscript(path, True)
                else:
                    runrootscript(path, False)
            elif type == 'userscript':
                if stage == 'setupassistant':
                    iaslog('Detected setupassistant and user script. \
                          User scripts cannot work in setupassistant stage! \
                          Removing %s') % path
                    os.remove(path)
                    pass
                if 'url' in item:
                    download_if_needed(item, stage, type, opts,
                                       depnotifystatus)
                iaslog('Triggering LaunchAgent for user script: %s' % (path))
                touch(userscripttouchpath)
                if opts.depnotify:
                    if depnotifystatus:
                        deplog('Status: Installing: %s' % (name))
                while os.path.isfile(userscripttouchpath):
                    iaslog('Waiting for user script to complete: %s' % (path))
                    time.sleep(0.5)

    # Kill the launchdaemon and agent
    try:
        os.remove(ialdpath)
    except:  # noqa
        pass
    try:
        os.remove(ialapath)
    except:  # noqa
        pass
    iaslog('Removing LaunchAgent from launchctl list: ' + opts.laidentifier)
    launchctl('/bin/launchctl', 'asuser', str(getconsoleuser()[1]),
              '/bin/launchctl', 'remove', opts.laidentifier)

    # Kill the bootstrap path.
    try:
        shutil.rmtree(iapath)
    except:  # noqa
        pass

    # Trigger the final DEPNotify events
    if opts.depnotify:
        for varg in opts.depnotify:
            notification = str(varg)
            if any(x in notification for x in deptriggers):
                iaslog('Sending %s to DEPNotify' % (str(notification)))
                deplog(notification)
            else:
                iaslog(
                    'Skipping DEPNotify notification event due to completion.')

    # Trigger a reboot
    if opts.reboot:
        subprocess.call(['/sbin/shutdown', '-r', 'now'])
    else:
        iaslog(
            'Removing LaunchDaemon from launchctl list: ' + opts.ldidentifier)
        launchctl('/bin/launchctl', 'remove', opts.ldidentifier)


if __name__ == '__main__':
    main()
