import os
import sys
import textwrap
import time

import humanize
import oyaml as yaml
from cloudmesh.burn.image import Image
from cloudmesh.burn.usb import USB
from cloudmesh.burn.util import os_is_linux
from cloudmesh.burn.util import os_is_mac
from cloudmesh.burn.util import os_is_pi
from cloudmesh.common.Shell import Shell
from cloudmesh.common.Shell import windows_not_supported
from cloudmesh.common.Tabulate import Printer
from cloudmesh.common.console import Console
from cloudmesh.common.sudo import Sudo
from cloudmesh.common.systeminfo import get_platform
from cloudmesh.common.util import banner
from cloudmesh.common.util import path_expand
from cloudmesh.common.util import readfile as common_readfile
from cloudmesh.common.util import yn_choice


def _execute(msg, command):
    Console.ok(msg)
    try:
        os.system(command.strip())
    except:
        Console.error("{command} failed")
        # but ignore error

def location(host_os=None, card_os="raspberry", volume="boot"):
    """
    Returns the location of the specific volume after mounting

    @param volume: either boot or root
    @type volume: str
    @param host_os: The OS on which this command is run and the mount occurs.
                    Values are mac, raspberry, ubuntu, linux. Linux maps to ubuntu
    @type host_os: str
    @param card_os: The operating system to be burned on the card.
                    Values are raspberry, ubuntu
    @type card_os: str
    @return:
    @rtype:
    """

    # for backwards compatibility map linux to ubuntu
    if host_os.lower() in ["linux"]:
        host_os = "ubuntu"

    user = os.environ.get('USER')

    # where [host_os][burn_os][volume]
    where = yaml.safe_load(
        textwrap.dedent(f"""
        raspberry: 
          raspberry:
            root: /media/{user}/rootfs
            boot: /media/{user}/boot
          ubuntu: 
            root: /media/{user}/writable
            boot: /media/{user}/system-boot
        mac:
          raspberry:
            root: /Volumes/rootfs
            boot: /Volumes/boot
          ubuntu: 
            root: /Volumes/writable
            boot: /Volume/system-boot
        ubuntu: 
          raspberry:
            root: /media/{user}/rootfs
            boot: /media/{user}/boot
          ubuntu: 
            root: /media/{user}/writable
            boot: /media/{user}/system-boot
        """)
    )
    try:
        return where[host_os][card_os][volume]
    except Exception as e:
        print(e)
        return "undefined"


def execute(command=None, decode="True", debug=False):
    """
    Executes the command

    :param command: The command to run
    :type command: list or str
    :return:
    :rtype:
    """
    result = Sudo.execute(command, decode=decode, debug=debug)
    return result


class SDCard:

    def __init__(self, card_os=None, host_os=None):
        """
        Creates mount point strings based on OS and the host where it is executed

        :param card_os: the os that is part of the mount. Default: raspberry
        :type card_os: str
        :param host_os: the host on which we execute the command
        :type host_os: possible values: raspberry, macos, linux
        """
        self.card_os = card_os or "raspberry"
        self.host_os = host_os or get_platform()

    @property
    def root_volume(self):
        """
        the location of system volume on the SD card for the specified host
        and os in Location initialization

        TODO: not implemented

        :return: the location
        :rtype: str
        """
        return location(volume="root", card_os=self.card_os, host_os=self.host_os)

    @property
    def boot_volume(self):
        """
        the location of the boot volume for the specified host and os in
        Location initialization

        :return: the location
        :rtype: str
        """
        return location(volume="boot", card_os=self.card_os, host_os=self.host_os)

    def ls(self):
        """
        List all file systems on the SDCard. This is for the PI rootfs and boot

        :return: A dict representing the file systems on the SDCCards
        :rtype: dict
        """

        r = Shell.run("mount -l").splitlines()
        root_fs = self.root_volume
        boot_fs = self.boot_volume

        details = {}
        for line in r:
            if str(root_fs) in line or str(boot_fs) in line:
                entry = \
                    line.replace(" on ", "|") \
                        .replace(" type ", "|") \
                        .replace(" (", "|") \
                        .replace(") [", "|") \
                        .replace("]", "") \
                        .split("|")
                # print(entry)
                detail = {
                    "device": entry[0],
                    "path": entry[1],
                    "type": entry[2],
                    "parameters": entry[3],
                    "name": entry[4],
                }
                details[detail["name"]] = detail
        return details

    @staticmethod
    def readfile(filename=None, split=False, trim=False, decode=True):
        """
        Reads the content of the file as sudo and returns the result

        :param filename: the filename
        :type filename: str
        :param split: uf true returns a list of lines
        :type split: bool
        :param trim: trim trailing whitespace. This is useful to
                     prevent empty string entries when splitting by '\n'
        :type trim: bool
        :return: the content
        :rtype: str or list
        """
        os.system("sync")

        if os_is_mac():
            if decode:
                mode = "r"
            else:
                mode = "rb"
            content = common_readfile(filename, mode=mode)
        else:
            Sudo.password()
            result = Sudo.execute(f"cat {filename}", decode=decode)
            content = result.stdout

        if trim:
            content = content.rstrip()

        if split:
            content = content.splitlines()

        return content

    @staticmethod
    def writefile(filename=None, content=None, append=False):
        """
        Writes the content in the the given file.

        :param filename: the filename
        :type filename: str
        :param content: the content
        :type content: str
        :param append: if true it append it at the end, otherwise the file will
                       be overwritten
        :type append: bool
        :return: the output created by the write process
        :rtype: int
        """
        os.system("sync")
        if append:
            content = Sudo.readfile(filename, split=False, decode=True) + content
        os.system(f"echo '{content}' | sudo cp /dev/stdin {filename}")
        os.system("sync")

        return content

    @staticmethod
    def size(device="/dev/sdX"):

        size = 64 * 1000 ** 3   # this is a bug as we need that for Linux and PI

        if os_is_mac():
            result = Shell.run("diskutil list external").splitlines()
            for line in result:
                if "FDisk_partition_scheme" in line:
                    data = line.split()
                    size, unit = data[2].replace("*", ""), data[3]
                    if unit == "GB":
                        size = int(float(size) * 1000**3)
                    else:
                        size = 64
                        Console.error("Unit not GB")
                    break
        elif os_is_linux():
            try:
                result = Shell.run(f"sudo blockdev --getsize64 {device}").strip()
                result = int(result)
            except Exception as e:  # noqa: F841
                Console.error(f"Could not determine size of the device {device}")
                sys.exit()
        return size

    @windows_not_supported
    def format_device(self,
                      device='dev/sdX',
                      unmount=False,
                      yes=False,
                      verbose=True):
        """
        Formats device with one FAT32 partition

        WARNING: make sure you have the right device, this command could
                 potentially erase your OS

        :param device: The device on which we format
        :type device: str
        """

        _title = "UNTITLED"

        def prepare_sdcard():
            """
            ensures a card is detected and unmounted
            :return: True if prepared
            :rtype: bool
            """
            #
            Console.ok(f'sudo eject -t {device}')
            os.system(f'sudo eject -t {device}')
            time.sleep(3)
            device_basename = os.path.basename(device)
            result = Shell.run('lsblk')
            if device_basename in result.split():
                for line in result.splitlines():
                    line = line.split()
                    if device_basename in line[0] and len(line) > 6:
                        Console.ok(f'sudo umount {line[6]}')
                        os.system(f'sudo umount {line[6]}')
                return True
            else:
                Console.error("SD Card not detected. Please reinsert "
                              "card reader. ")
                if not yn_choice("Card reader re-inserted? No to cancel "
                                 "operation"):
                    return False
                else:
                    time.sleep(3)
                    return prepare_sdcard()

        Sudo.password()
        if os_is_linux() or os_is_pi():

            if verbose:
                banner(f"format {device}")
            else:
                print(f"format {device}")

            if not prepare_sdcard():
                return False

            self.mount(device=device)
            user = os.environ.get('USER')

            script = textwrap.dedent(f"""
                ls /media/{user}
                sudo parted {device} --script -- mklabel msdos
                sudo parted {device} --script -- mkpart primary fat32 1MiB 100%
                sudo mkfs.vfat -n {_title} -F32 {device}1
                sudo parted {device} --script print""").strip().splitlines()
            for line in script:
                _execute(line, line)

            os.system("sudo sync")
            if unmount:
                self.unmount(device=device)  # without dev we unmount but do not eject. If
                # we completely eject, burn will fail to detect the device.
                os.system("sudo sync")

            Console.ok("Formatted SD Card")

        elif os_is_mac():

            details = USB.get_dev_from_diskutil()

            # checking if string contains list element
            valid = any(entry in device for entry in details)

            if not valid:
                Console.error(f"this device can not be used for formatting: {device}")
                return False

            elif len(details) > 1:
                Console.error("For security reasons, please only put one USB writer in")
                Console.msg(f"we found {details}")
                return False

            else:

                details = USB.get_from_diskutil()

                USB.print_details(details)
                print()
                if yes or yn_choice(f"\nDo you like to format {device} as {_title}"):
                    _execute(f"Formatting {device} as {_title}",
                             f"sudo diskutil eraseDisk FAT32 {_title} MBRFormat {device}")

        else:
            raise NotImplementedError("Not implemented for this OS")

        return True

    def _info(self):
        print ("root", self.root_volume)
        print ("boot", self.boot_volume)

    # @windows_not_supported
    def mount(self, device=None, card_os=None):
        """
        Mounts the current SD card
        """
        Sudo.password()
        host = get_platform()

        if os_is_linux():
            dmesg = USB.get_from_dmesg()

            # TODO Need a better way to identify which sd card to use for mounting
            # instead of iterating over all of them

            for usbcard in dmesg:

                dev = device or usbcard['dev']
                print(f"Mounting filesystems on {dev}")
                try:
                    Console.ok(f"mounting {device}")
                    os.system('sudo sync')  # flush any pending/in-process writes
                    os.system(f"sudo eject -t {device}")
                    os.system('sudo sync')  # flush any pending/in-process writes

                except Exception as e:
                    print(e)

        elif os_is_pi() :

            if card_os is None:
                Console.error("Please specify the OS you have on the SD Card")
                return ""
            self.card_os = card_os
            dmesg = USB.get_from_dmesg()
            print (dmesg)

            # TODO Need a better way to identify which sd card to use for mounting
            # instead of iterating over all of them

            os.system('sudo sync')  # flush any pending/in-process writes

            for usbcard in dmesg:

                dev = device or usbcard['dev']
                print(f"Mounting filesystems on {dev} assuming it is {card_os} as you specified")
                sd1 = f"{dev}1"
                sd2 = f"{dev}2"
                try:
                    if os.path.exists(sd1):
                        Console.ok(f"mounting {sd1} {self.boot_volume}")
                        os.system(f"sudo mkdir -p {self.boot_volume}")
                        os.system(f"sudo mount -t vfat {sd1} {self.boot_volume}")
                except Exception as e:
                    print(e)
                try:
                    if os.path.exists(sd2):
                        Console.ok(f"mounting {sd2} {self.root_volume}")
                        os.system(f"sudo mkdir -p {self.root_volume}")
                        os.system(f"sudo mount -t ext4 {sd2} {self.root_volume}")
                except Exception as e:
                    print(e)

        elif os_is_mac():

            dev = USB.get_dev_from_diskutil()[0]
            volumes = [
                {"dev": f"{dev}s1", "mount": self.boot_volume},
                {"dev": f"{dev}s2", "mount": self.root_volume},
            ]
            for volume in volumes:

                dev = str(volume['dev'])
                mount = volume['mount']
                try:
                    if not os.path.exists(mount):
                        os.system(f"sudo mkdir -p {mount}")
                        os.system(f"sudo mount -t vfat {dev} {mount}")
                except Exception as e:
                    print(e)
            for volume in volumes:
                dev = str(volume['dev'])
                mount = volume['mount']

                if os.path.exists(mount):
                    Console.ok(f"Mounted {mount}")
                else:
                    Console.error(f"Could not mounted {mount}")

        else:
            Console.error("Not yet implemented for your OS")
        return ""


    @windows_not_supported
    def unmount(self, device=None, card_os="raspberry"):
        """
        Unmounts the current SD card

        :param device: device to unmount, e.g. /dev/sda
        :type device: str
        """

        Sudo.password()

        host = get_platform()
        self.card_os = card_os

        os.system('sudo sync')  # flush any pending/in-process writes



        os.system("sync")
        if os_is_linux() or os_is_pi():
            _execute(f"eject {device}", f"sudo eject {device}")
            # _execute(f"unmounting {self.boot_volume}", f"sudo umount {self.boot_volume}")
            # _execute(f"unmounting  {self.root_volume}", f"sudo umount {self.root_volume}")
        elif os_is_mac():

            _execute(f"unmounting {self.boot_volume}", f"diskutil umountDisk {device}")

        else:
            Console.error("Not yet implemented for your OS")
            return ""
        os.system("sync")
        #rm = [f"sudo rmdir {self.boot_volume}",
        #      f"sudo rmdir {self.root_volume}"]

        #for command in rm:
        #    _execute(command, command)

        return True

    @windows_not_supported
    def load_device(self, device='dev/sdX'):
        """
        Loads the USB device via trayload

        :param device: The device on which we format
        :type device: str
        """
        if os_is_linux() or os_is_pi():
            banner(f"load {device}")
            Sudo.password()
            os.system(f"sudo eject -t {device}")

        else:
            raise Console.error("Not implemented for this OS")

    @windows_not_supported
    def backup(self, device=None, to_file=None, blocksize="4m"):
        if device is None:
            Console.error("Device must have a value")
        if to_file is None:
            Console.error("To file must have a value")
        else:
            Sudo.password()

            to_file = path_expand(to_file)

            size = SDCard.size(device)

            to_file = path_expand(to_file)

            #
            # speed up burning on MacOS
            #
            if device.startswith("/dev/disk"):
                device = device.replace("/dev/disk", "/dev/rdisk")

            command = f"sudo dd if={device} bs={blocksize} |" \
                      f' tqdm --bytes --total {size} --ncols 80|' \
                      f"dd of={to_file} bs={blocksize}"

            print()
            Console.info(command)
            print()

            os.system(command)

    @windows_not_supported
    def burn_sdcard(self,
                    image=None,
                    tag=None,
                    device=None,
                    blocksize="4M",
                    name="the inserted card",
                    yes=False):
        """
        Burns the SD Card with an image

        :param image: Image object to use for burning (used by copy)
        :type image: str
        :param tag: tag object used for burning (used by sdcard)
        :type tag: str
        :param device: Device to burn to, e.g. /dev/sda
        :type device: str
        :param blocksize: the blocksize used when writing, default 4M
        :type blocksize: str
        """
        if image and tag:
            Console.error("Implementation error, burn_sdcard can't have image "
                          "and tag.")
            return ""

        Console.info(f"Burning {name} ...")
        if image is not None:
            image_path = image
        else:
            image = Image().find(tag=tag)

            if image is None:
                Console.error("No matching image found.")
                return ""
            elif len(image) > 1:
                Console.error("Too many images found")
                print(Printer.write(image,
                                    order=["tag", "version"],
                                    header=["Tag", "Version"]))
                return ""

            image = image[0]

            if "ubuntu" in image["url"]:
                _name = os.path.basename(Image.get_name(image["url"]))
                _name = _name.replace(".xz", "")
            else:
                _name = os.path.basename(Image.get_name(image["url"])) + ".img"

            image_path = Image().directory + "/" + _name

            print (image_path)

            if not os.path.isfile(image_path):
                tags = ' '.join(tag)

                print()
                Console.error(f"Image with tags '{tags}' not found. To download use")
                print()
                Console.msg(f"cms burn image get {tags}")
                print()
                return ""

        orig_size = size = humanize.naturalsize(os.path.getsize(image_path))

        # size = details[0]['size']
        n, unit = size.split(" ")
        unit = unit.replace("GB", "G")
        unit = unit.replace("MB", "M")
        n = float(n)
        if unit == "G":
            n = n * 1000 ** 3
        elif unit == "M":
            n = n * 1000 ** 2
        size = int(n)

        banner(f"Preparing the SDCard {name}")
        print(f"Name:       {name}")
        print(f"Image:      {image_path}")
        print(f"Image Size: {orig_size}")
        print(f"Device:     {device}")
        print(f"Blocksize:  {blocksize}")

        print()

        Sudo.password()

        if device is None:
            Console.error("Please specify a device")
            return

        #
        # speedup burn for MacOS
        #
        if device.startswith("/dev/disk"):
            device = device.replace("/dev/disk", "/dev/rdisk")

        if os_is_mac():
            details = USB.get_from_diskutil()
            USB.print_details(details)


        if not(yes or yn_choice(f"\nDo you like to write {name} on {device} "
                                f"with the image {image_path}")):
            return ""

        self.mount(device=device)

        # blocksize = blocksize.replace("M", "m")

        if os_is_mac():
            command = f"sudo dd if={image_path} bs={blocksize} |" \
                      f' tqdm --bytes --total {size} --ncols 80 |' \
                      f" sudo dd of={device} bs={blocksize} conv=fsync"
        else:
            # command = f"sudo dd if={image_path} of={device} bs={blocksize} status=progress conv=fsync"
            command = f"sudo dd if={image_path} bs={blocksize} oflag=direct |" \
                      f' tqdm --bytes --total {size} --ncols 80 |' \
                      f" sudo dd of={device} bs={blocksize} oflag=direct conv=fsync"
        print(command)
        os.system(command)

        Sudo.execute("sync")
        self.unmount(device=device)

