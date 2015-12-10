# Copyright (c) 2015 SUSE Linux GmbH.  All rights reserved.
#
# This file is part of kiwi.
#
# kiwi is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# kiwi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with kiwi.  If not, see <http://www.gnu.org/licenses/>
#
from tempfile import mkdtemp
import os

# project
from command import Command
from bootloader_config import BootLoaderConfig
from filesystem_squashfs import FileSystemSquashFs
from filesystem_isofs import FileSystemIsoFs
from image_identifier import ImageIdentifier
from path import Path
from checksum import Checksum
from logger import log
from kernel import Kernel
from iso import Iso

from exceptions import (
    KiwiInstallBootImageError
)


class InstallImageBuilder(object):
    """
        Installation image builder
    """
    def __init__(self, xml_state, target_dir, boot_image_task):
        self.target_dir = target_dir
        self.machine = xml_state.get_build_type_machine_section()
        self.boot_image_task = boot_image_task
        self.xml_state = xml_state
        self.diskname = ''.join(
            [
                target_dir, '/',
                xml_state.xml_data.get_name(), '.raw'
            ]
        )
        self.isoname = ''.join(
            [
                target_dir, '/',
                xml_state.xml_data.get_name(), '.install.iso'
            ]
        )
        self.md5name = ''.join(
            [xml_state.xml_data.get_name(), '.md5']
        )

        self.mbrid = ImageIdentifier()
        self.mbrid.calculate_id()

        self.media_dir = None
        self.squashed_contents = None
        self.custom_iso_args = None

    def create_install_iso(self):
        """
            Create an install ISO from the disk_image as hybrid ISO
            bootable via legacy BIOS, EFI and as disk from Stick
        """
        self.media_dir = mkdtemp(
            prefix='install-media.', dir=self.target_dir
        )
        # custom iso metadata
        self.custom_iso_args = [
            '-V', '"KIWI Installation System"',
            '-A', self.mbrid.get_id()
        ]

        # the install image transfer is checked against a checksum
        log.info('Creating disk image checksum')
        self.squashed_contents = mkdtemp(
            prefix='install-squashfs.', dir=self.target_dir
        )
        checksum = Checksum(self.diskname)
        checksum.md5(self.squashed_contents + '/' + self.md5name)

        # the kiwi initrd code triggers the install by trigger files
        self.__create_iso_install_trigger_files()

        # the install image is stored as squashfs embedded file
        log.info('Creating squashfs embedded disk image')
        Command.run(
            ['cp', '-l', self.diskname, self.squashed_contents]
        )
        squashed_image_file = self.diskname + '.squashfs'
        squashed_image = FileSystemSquashFs(
            device_provider=None, source_dir=self.squashed_contents
        )
        squashed_image.create_on_file(squashed_image_file)
        Command.run(
            ['mv', squashed_image_file, self.media_dir]
        )

        # setup bootloader config to boot the ISO via isolinux
        log.info('Setting up install image bootloader configuration')
        bootloader_config_isolinux = BootLoaderConfig(
            'isolinux', self.xml_state, self.media_dir
        )
        bootloader_config_isolinux.setup_install_boot_images(
            mbrid=None,
            lookup_path=self.boot_image_task.boot_root_directory
        )
        bootloader_config_isolinux.setup_install_image_config(
            mbrid=None
        )
        bootloader_config_isolinux.write()

        # setup bootloader config to boot the ISO via EFI
        bootloader_config_grub = BootLoaderConfig(
            'grub2', self.xml_state, self.media_dir
        )
        bootloader_config_grub.setup_install_boot_images(
            mbrid=self.mbrid,
            lookup_path=self.boot_image_task.boot_root_directory
        )
        bootloader_config_grub.setup_install_image_config(
            mbrid=self.mbrid
        )
        bootloader_config_grub.write()

        # create initrd for install image
        log.info('Creating install image boot image')
        self.__create_iso_install_kernel_and_initrd()

        # create iso filesystem from media_dir
        log.info('Creating ISO filesystem')
        iso_image = FileSystemIsoFs(
            device_provider=None,
            source_dir=self.media_dir,
            custom_args=self.custom_iso_args
        )
        iso_header_offset = iso_image.create_on_file(self.isoname)

        # make it hybrid
        Iso.create_hybrid(
            iso_header_offset, self.mbrid, self.isoname
        )

    def create_install_pxe_archive(self):
        # TODO
        pass

    def __create_iso_install_kernel_and_initrd(self):
        boot_path = self.media_dir + '/boot/x86_64/loader'
        Path.create(boot_path)
        kernel = Kernel(self.boot_image_task.boot_root_directory)
        if kernel.get_kernel():
            kernel.copy_kernel(boot_path, '/linux')
        else:
            raise KiwiInstallBootImageError(
                'No kernel in boot image tree %s found' %
                self.boot_image_task.boot_root_directory
            )
        if self.machine and self.machine.get_domain() == 'dom0':
            if kernel.get_xen_hypervisor():
                kernel.copy_xen_hypervisor(boot_path, '/xen.gz')
            else:
                raise KiwiInstallBootImageError(
                    'No hypervisor in boot image tree %s found' %
                    self.boot_image_task.boot_root_directory
                )
        self.boot_image_task.create_initrd(self.mbrid)
        Command.run(
            [
                'mv', self.boot_image_task.initrd_filename,
                boot_path + '/initrd'
            ]
        )

    def __create_iso_install_trigger_files(self):
        disk_base_name = os.path.basename(self.diskname)
        initrd_trigger = \
            self.boot_image_task.boot_root_directory + '/config.vmxsystem'
        iso_trigger = self.media_dir + '/config.isoclient'
        with open(initrd_trigger, 'w') as vmx_system:
            vmx_system.write('IMAGE="%s"\n' % disk_base_name)
        with open(iso_trigger, 'w') as iso_system:
            iso_system.write('IMAGE="%s"\n' % disk_base_name)

    def __del__(self):
        log.info('Cleaning up %s instance', type(self).__name__)
        if self.media_dir:
            Path.wipe(self.media_dir)
        if self.squashed_contents:
            Path.wipe(self.squashed_contents)
