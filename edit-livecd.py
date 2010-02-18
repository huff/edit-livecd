#!/usr/bin/python -tt
#
# edit livecd: Edit a livecd to insert files
#
# Copyright 2009, Red Hat  Inc.
# Written by Perry Myers <pmyers@redhat.com> & David Huff <dhuff@redhat.com>
# 
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

import os
import sys
import tempfile
import shutil
import subprocess
import optparse
import logging

from imgcreate.debug import *
from imgcreate.fs import *
from imgcreate.live import *

class ExistingSparseLoopbackDisk(SparseLoopbackDisk):
    """don't want to expand the disk"""
    def __init__(self, lofile, size):
        SparseLoopbackDisk.__init__(self, lofile, size)
    
    def create(self):
        #self.expand(create = True)
        LoopbackDisk.create(self)

class LiveImageEditor(LiveImageCreator):
    """class for editing LiveCD images.
    
    We need an instance of LiveImageCreator however we do not have a kickstart 
    file nor do we need to create a new image. We just want to reuse some of 
    LiveImageCreators methods on an existing livecd image.   

    """
    
    def __init__(self, name):
        """Initialize a LiveImageEditor instance.

        creates a dummy instance of LiveImageCreator
        We do not initialize any sub classes b/c we have no ks file. 

        """
        self.name = name
        
        self.tmpdir = "/var/tmp"
        """The directory in which all temporary files will be created.""" 
           
        self.skip_compression = False
        """Controls whether to use squashfs to compress the image."""

        self.skip_minimize = False
        """Controls whether an image minimizing snapshot should be created."""  
        
        self._isofstype = "iso9660" 
        self.__isodir = None
        
        self._ImageCreator__builddir = None
        """working directory"""
                
        self._ImageCreator_instroot = None
        """where the extfs.img is mounted for modification"""
        
        self._ImageCreator_outdir = None
        """where final iso gets written"""
        
        self._ImageCreator__bindmounts = []
        
        self._LoopImageCreator__imagedir = None
        """dir for the extfs.img"""
        
        self._LoopImageCreator__blocksize = 4096
        self._LoopImageCreator__fslabel = None
        self._LoopImageCreator__instloop = None
        self._LoopImageCreator__fstype = None
        self._LoopImageCreator__image_size = None
        
        self._LiveImageCreatorBase__isodir = None
        """directory where the iso is staged"""  
        
    # properties
    def __get_image(self):
        if self._LoopImageCreator__imagedir is None:
            self.__ensure_builddir()
            self._LoopImageCreator__imagedir = tempfile.mkdtemp(dir = os.path.abspath(self.tmpdir), prefix = self.name + "-")
        return self._LoopImageCreator__imagedir + "/ext3fs.img"
    _image = property(__get_image)
    """The location of the image file"""


    def _get_fstype(self):
        dev_null = os.open("/dev/null", os.O_WRONLY)
        try:
            out = subprocess.Popen(["/sbin/blkid", self._image],
                                   stdout = subprocess.PIPE,
                                   stderr = dev_null).communicate()[0]
            for word in out.split():
                if word.startswith("TYPE"):
                    self._LoopImageCreator__fstype = word.split("=")[1].strip("\"")
        
        except IOError, e:
            raise CreatorError("Failed to determine fsimage TYPE: %s" % e )
        
        
    def _get_fslable(self):
        dev_null = os.open("/dev/null", os.O_WRONLY)
        try:
            out = subprocess.Popen(["/sbin/e2label", self._image],
                                   stdout = subprocess.PIPE,
                                   stderr = dev_null).communicate()[0]

            self._LoopImageCreator__fslable = out.strip()
        
        except IOError, e:
            raise CreatorError("Failed to determine fsimage TYPE: %s" % e )
    
    
    def __ensure_builddir(self):
        if not self._ImageCreator__builddir is None:
            return

        try:
            self._ImageCreator__builddir = tempfile.mkdtemp(dir =  os.path.abspath(self.tmpdir),
                                               prefix = "edit-livecd-")
        except OSError, (err, msg):
            raise CreatorError("Failed create build directory in %s: %s" %
                               (self.tmpdir, msg))
        
        
    def _run_script(self, script):
        
        (fd, path) = tempfile.mkstemp(prefix = "script-",
                                          dir = self._instroot + "/tmp")
        
        logging.debug("copying script to install root: %s" % path)
        shutil.copy(os.path.abspath(script), path)
        os.close(fd)
        os.chmod(path, 0700)
        
        script = "/tmp/" + os.path.basename(path)
        
             
        try:
            subprocess.call([script], preexec_fn = self._chroot)
        except OSError, e:
            raise CreatorError("Failed to execute script %s, %s " % (script, e))
        finally:
            os.unlink(path)       

        
    def mount(self, base_on, cachedir = None):
        """mount existing file system.  
    
        we have to override mount b/c we are not creating an new install root 
        nor do we need to setup the file system, ie makedirs(/etc/, /boot, ...),
        nor do we want to overwrite fstab, or create selinuxfs
               
        We also need to get some info about the image before we
        can mount it.
    
        """
        
        if not base_on:
            raise CreatorError("No base livecd image specified")
        
        self.__ensure_builddir()
        
        self._ImageCreator_instroot = self._ImageCreator__builddir + "/install_root"
        self._LoopImageCreator__imagedir = self._ImageCreator__builddir + "/ex"
        self._ImageCreator_outdir = self._ImageCreator__builddir + "/out"
                       
        makedirs(self._ImageCreator_instroot)
        makedirs(self._LoopImageCreator__imagedir)
        makedirs(self._ImageCreator_outdir)
        
        LiveImageCreator._base_on(self, base_on)

        self._LoopImageCreator__image_size = os.stat(self._image)[stat.ST_SIZE]
        self._get_fstype()
        self._get_fslable()
                
        self._LoopImageCreator__instloop = ExtDiskMount(ExistingSparseLoopbackDisk(self._image,
                                                                                   self._LoopImageCreator__image_size),
                                                        self._ImageCreator_instroot,
                                                        self._fstype,
                                                        self._LoopImageCreator__blocksize,
                                                        self.fslabel)
        try:
            self._LoopImageCreator__instloop.mount()
        except MountError, e:
            raise CreatorError("Failed to loopback mount '%s' : %s" %
                               (self._image, e))

        cachesrc = cachedir or (self._ImageCreator__builddir + "/yum-cache")
        makedirs(cachesrc)

        for (f, dest) in [("/sys", None), ("/proc", None),
                          ("/dev/pts", None), ("/dev/shm", None),
                          (cachesrc, "/var/cache/yum")]:
            self._ImageCreator__bindmounts.append(BindChrootMount(f, self._instroot, dest))

        self._do_bindmounts()
        
        os.symlink("../proc/mounts", self._instroot + "/etc/mtab")
        
        self.__copy_cd_root(base_on)
        
    
    def __copy_cd_root(self, base_on):
        """helper function to root content of the base liveCD to ISOdir"""
        
        isoloop = DiskMount(LoopbackDisk(base_on, 0), self._mkdtemp())
        self._LiveImageCreatorBase__isodir = self._ImageCreator__builddir + "/iso"

        try:
            isoloop.mount()
            # legacy LiveOS filesystem layout support, remove for F9 or F10
            if os.path.exists(isoloop.mountdir + "/squashfs.img"):
                squashimg = isoloop.mountdir + "/squashfs.img"
            else:
                squashimg = isoloop.mountdir + "/LiveOS/squashfs.img"
                
            #copy over everything but squashimg
            shutil.copytree(isoloop.mountdir, 
                            self._LiveImageCreatorBase__isodir, 
                            ignore=shutil.ignore_patterns("squashfs.img", "osmin.img"))
        except MountError, e:
            raise CreatorError("Failed to loopback mount '%s' : %s" %
                               (base_on, e))
            
        finally:
            isoloop.cleanup()
    
    
def parse_options(args):
    parser = optparse.OptionParser(usage = "%prog [-s=<script.sh>] <LIVECD.iso>")
    
    parser.add_option("-n", "--name", type="string", dest="name",
                      help="name of new livecd (don't include .iso will be added)")

    parser.add_option("-o", "--output", type="string", dest="output",
                      help="specify the output dir")
    
    parser.add_option("-s", "--script", type="string", dest="script",
                      help="specify script to run chrooted in the livecd fsimage")
    
    parser.add_option("-t", "--tmpdir", type="string",
                      dest="tmpdir", default="/var/tmp",
                      help="Temporary directory to use (default: /var/tmp)")
    
    parser.add_option("", "--skip-compression", action="store_true", dest="skip_compression")
    
    parser.add_option("", "--skip-minimize", action="store_true", dest="skip_minimize")
    
    setup_logging(parser)

    (options, args) = parser.parse_args()

    if len(args) != 1:
        parser.print_usage()
        sys.exit(1)

    return (args[0], options)


def main():
    (livecd, options) = parse_options(sys.argv[1:])

    if os.geteuid () != 0:
        print >> sys.stderr, "You must run edit-livecd as root"
        return 1

    if options.name:
        name = options.name
    else:
        name = os.path.basename(livecd) + ".edited"
        
    if options.output:
        output = options.output
    else:
        output = os.path.dirname(livecd)
        
        
    editor = LiveImageEditor(name)
    editor.tmpdir = os.path.abspath(options.tmpdir)
    editor.skip_compression = options.skip_compression
    editor.skip_minimize = options.skip_minimize
    
    try:
        editor.mount(livecd, cachedir = None)
        if options.script:
            print "Running edit script '%s'" % options.script
            editor._run_script(options.script)
        else:
            print "Launching shell. Exit to continue."
            print "----------------------------------"
            editor.launch_shell()
        editor.unmount()
        editor.package(output)
    except CreatorError, e:
        logging.error(u"Error editing Live CD : %s" % e)
        return 1
    finally:
        editor.cleanup()

    return 0
    

if __name__ == "__main__":
    sys.exit(main())
    

arch = rpmUtils.arch.getBaseArch()
if arch in ("i386", "x86_64"):
    LiveImageCreator = x86LiveImageCreator
elif arch in ("ppc",):
    LiveImageCreator = ppcLiveImageCreator
elif arch in ("ppc64",):
    LiveImageCreator = ppc64LiveImageCreator
else:
    raise CreatorError("Architecture not supported!")
