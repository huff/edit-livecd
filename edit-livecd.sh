#!/bin/bash
#
# Edit a livecd to insert files
# Copyright 2008 Red Hat, Inc.
# Written by Perry Myers <pmyers@redhat.com>
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
#!/bin/bash

PATH=$PATH:/sbin:/usr/sbin

ME=$(basename "$0")
warn() { printf '%s: %s\n' "$ME" "$*" >&2; }
try_h() { printf "Try \`$ME -h' for more information.\n" >&2; }
die() { warn "$@"; try_h; exit 1; }

NODEIMG_DEFAULT=/usr/share/ovirt-node-image/ovirt-node-image.iso
CD=$NODEIMG_DEFAULT

usage() {
    case $# in 1) warn "$1"; try_h; exit 1;; esac
    cat <<EOF
Usage: $ME -i LiveCD.iso [-b bootparams] [-p program]
  -b BOOTPARAMS  optional parameters appended to the kernel command line
  -i LIVECD.iso  LiveCD ISO to edit (default: $NODEIMG_DEFAULT)
  -o OUTPUT.iso  specify the output file (required)
  -p CODE        Arbitrary CODE that is eval'd while 'cd'd into the root of
                   the livecd root filesystem.  Note; the code is not run in
                   a chroot environment, so it can access the host filesystem.
                   If this option is omitted, this program pauses and allows
                   the user (in another terminal) to modify the filesystem
                   manually.  Type <enter> when done, and the script
                   re-packages the ISO.
  -h             display this help and exit

EXAMPLES

  Example Script:
    #!/bin/sh
    touch etc/sysconfig/foo
  Save as foo and make executable:
    chmod a+x foo
  Run this to create a file /etc/sysconfig/foo in the livecd filesystem
  (note the use of "\$PWD/foo", not "./foo", since it will be run from a
   different directory):

    $ME -i input.iso -o /tmp/result.iso -p "\$PWD/foo"

  or, equivalently, but without a separate script:

    $ME -i input.iso -o /tmp/result.iso -p 'touch etc/sysconfig/foo'

EOF
}

# exit after any error:
set -e

CODE=
OUTPUT_FILE=

err=0 help=0
while getopts :b:hi:o:p: c; do
    case $c in
        i) CD=$OPTARG;;
        b) PARAMS=$OPTARG;;
        o) OUTPUT_FILE=$OPTARG;;
        p) CODE=$OPTARG;;
        h) help=1;;
        '?') err=1; warn "invalid option: \`-$OPTARG'";;
        :) err=1; warn "missing argument to \`-$OPTARG' option";;
        *) err=1; warn "internal error: \`-$OPTARG' not handled";;
    esac
done
test $err = 1 && { try_h; exit 1; }
test $help = 1 && { usage; exit 0; }

# Require "-o OUTPUT_FILE"
test -z "$OUTPUT_FILE" \
  && { warn "no output file specified; use -o FILE.iso"; try_h; exit 1; }

# Fail if there are any extra command-line arguments.
if test $OPTIND -le $#; then
  bad_arg=$(eval "echo \$$OPTIND")
  warn "extra argument '$bad_arg'"; try_h; exit 1
fi

# first, check to see we are root
if [ $( id -u ) -ne 0 ]; then
    die "Must run as root"
fi

# Check for some prerequisites.
# "type" prints "PROG not found" if it's not in $PATH.
type mkisofs
type mksquashfs
type sed
type implantisomd5

sane_name()
{
  case $1 in
    *[^a-zA-Z0-9._,+:/@%=-]*) false;;
    *) true;;
  esac
}

# Fail if names we'll use contain white space or shell meta-characters
sane_name "$PWD" || die "invalid working directory name: $PWD"
sane_name "$CD" || die "invalid ISO name: $CD"

WDIR=`mktemp -d $PWD/livecd.XXXXXXXXXX`

addExit() {
    EXIT="$@ ; $EXIT"
    trap "$EXIT" EXIT HUP TERM INT QUIT
}

mnt() {
    local margs="$1" ; shift
    local mp="$WDIR/$1"
    for D in "$@" ; do
        mkdir -v -p "$WDIR/$D"
    done
    eval mount -v $margs "$mp"
    addExit "df | grep $mp > /dev/null 2>&1 && umount -v $mp"
}

addExit "rm -rf $WDIR"

ID_FS_LABEL= # initialize, in case vol_id fails
eval "$(/lib/udev/vol_id $CD)"
LABEL=$ID_FS_LABEL

# mount the CD image
mnt "-t iso9660 $CD -o loop,ro" cd

# mount compressed filesystem
mnt "-t squashfs $WDIR/cd/LiveOS/squashfs.img -o ro,loop" sq

# create writable copy of the new filesystem for the CD
cp -pr $WDIR/cd $WDIR/cd-w

# create writable copy of the filesystem for the new compressed
# squashfs filesystem
cp -pr $WDIR/sq $WDIR/sq-w

# mount root filesystem
mnt "-t ext2 $WDIR/sq-w/LiveOS/ext3fs.img -o rw,loop" ex

echo ">>> Updating CD content"
if [ -n "$CODE" ]; then
    (
      cd $WDIR/ex
      set +e
      eval "$CODE"
      set -e
    )
else
    echo "***"
    echo "*** Pausing to allow manual changes.  Press any key to continue."
    echo "***"
    read
fi

# Try to unmount.  But this is likely to fail, so let the user retry,
# e.g., if he forgot to "cd" out of $WDIR/ex.
while :; do
  echo ">>> Unmounting ext3fs"
  umount $WDIR/ex && break
  echo ">>> Unmounting the working file system copy failed"
  echo "***"
  echo "*** Did you forget to 'cd' out of $WDIR/ex?"
  echo "***"
  echo "*** Press any key to repeat the attempt."
  echo "***"
  read
done

echo ">>> Compressing filesystem"
mksquashfs $WDIR/sq-w/ $WDIR/cd-w/LiveOS/squashfs.img -noappend

echo ">>> Recomputing MD5 sums"
( cd $WDIR/cd-w && find . -type f -not -name md5sum.txt \
    -not -path '*/isolinux/*' -print0 | xargs -0 -- md5sum > md5sum.txt )

if [ -n "$PARAMS" ]; then
    case $PARAMS in
      *@*) warn "PARAMS contains the @ sed delimiter, be sure it's escaped";;
    esac
    echo ">>> Appending boot parameters"
    sed -i 's@^  append .*$@& '"$PARAMS@" "$WDIR/cd-w/isolinux/isolinux.cfg"
fi

echo ">>> Creating ISO image $ISO"
mkisofs \
    -V "$LABEL" \
    -r -cache-inodes -J -l \
    -b isolinux/isolinux.bin \
    -c isolinux/boot.cat \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    -o "$OUTPUT_FILE" \
    $WDIR/cd-w

echo ">>> Implanting ISO MD5 Sum"
implantisomd5 --force "$OUTPUT_FILE"

# The trap ... callbacks will unmount everything.
set +e
