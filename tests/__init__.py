#!/usr/bin/python2 -O
# vim: fileencoding=utf-8

#
# The Qubes OS Project, https://www.qubes-os.org/
#
# Copyright (C) 2014-2015
#                   Marek Marczykowski-Górecki <marmarek@invisiblethingslab.com>
# Copyright (C) 2015  Wojtek Porczyk <woju@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#

import multiprocessing
import os
import shutil
import unittest

import libvirt

import qubes.backup
import qubes.qubes

VMPREFIX = 'test-'

class _AssertNotRaisesContext(object):
    """A context manager used to implement TestCase.assertNotRaises methods.

    Stolen from unittest and hacked. Regexp support stripped.
    """

    def __init__(self, expected, test_case, expected_regexp=None):
        self.expected = expected
        self.failureException = test_case.failureException

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        if exc_type is None:
            return True

        try:
            exc_name = self.expected.__name__
        except AttributeError:
            exc_name = str(self.expected)

        if issubclass(exc_type, self.expected):
            raise self.failureException(
                "{0} raised".format(exc_name))
        else:
            # pass through
            return False

        self.exception = exc_value # store for later retrieval



class SystemTestsMixin(object):
    def setUp(self):
        '''Set up the test.

        .. warning::
            This method instantiates QubesVmCollection acquires write lock for
            it. You can use is as :py:attr:`qc`. Do not release the lock
            yourself.
        '''

        super(SystemTestsMixin, self).setUp()

        self.qc = qubes.qubes.QubesVmCollection()
        self.qc.lock_db_for_writing()
        self.qc.load()

        self.conn = libvirt.open(qubes.qubes.defaults['libvirt_uri'])

        self.remove_test_vms()


    def tearDown(self):
        super(SystemTestsMixin, self).tearDown()

        self.remove_test_vms()

        self.qc.save()
        self.qc.unlock_db()
        del self.qc

        self.conn.close()


    def assertNotRaises(self, excClass, callableObj=None, *args, **kwargs):
        """Fail if an exception of class excClass is raised
           by callableObj when invoked with arguments args and keyword
           arguments kwargs. If a different type of exception is
           raised, it will not be caught, and the test case will be
           deemed to have suffered an error, exactly as for an
           unexpected exception.

           If called with callableObj omitted or None, will return a
           context object used like this::

                with self.assertRaises(SomeException):
                    do_something()

           The context manager keeps a reference to the exception as
           the 'exception' attribute. This allows you to inspect the
           exception after the assertion::

               with self.assertRaises(SomeException) as cm:
                   do_something()
               the_exception = cm.exception
               self.assertEqual(the_exception.error_code, 3)
        """
        context = _AssertNotRaisesContext(excClass, self)
        if callableObj is None:
            return context
        with context:
            callableObj(*args, **kwargs)


    def make_vm_name(self, name):
        return VMPREFIX + name


    def _remove_vm_qubes(self, vm):
        vmname = vm.name

        try:
            # XXX .is_running() may throw libvirtError if undefined
            if vm.is_running():
                vm.force_shutdown()
        except: pass

        try: vm.remove_from_disk()
        except: pass

        try: vm.libvirtDomain.undefine()
        except (AttributeError, libvirt.libvirtError): pass

        self.qc.pop(vm.qid)
        self.qc.save()
        del vm

        # Now ensure it really went away. This may not have happened,
        # for example if vm.libvirtDomain malfunctioned.
        try:
            dom = self.conn.lookupByName(vmname)
        except: pass
        else:
            self._remove_vm_libvirt(dom)

        self._remove_vm_disk(vmname)


    def _remove_vm_libvirt(self, dom):
        try:
            dom.destroy()
        except libvirt.libvirtError: # not running
            pass
        dom.undefine()


    def _remove_vm_disk(self, vmname):
        for dirspec in (
                'qubes_appvms_dir',
                'qubes_servicevms_dir',
                'qubes_templates_dir'):
            dirpath = os.path.join(qubes.qubes.system_path['qubes_base_dir'],
                qubes.qubes.system_path[dirspec], vmname)
            if os.path.exists(dirpath):
                if os.path.isdir(dirpath):
                    shutil.rmtree(dirpath)
                else:
                    os.unlink(dirpath)


    def remove_vms(self, vms):
        for vm in vms: self._remove_vm_qubes(vm)


    def remove_test_vms(self):
        '''Aggresively remove any domain that has name in testing namespace.

        .. warning::
            The test suite hereby claims any domain whose name starts with
            :py:data:`VMPREFIX` as fair game. This is needed to enforce sane
            test executing environment. If you have domains named ``test-*``,
            don't run the tests.
        '''

        # first, remove them Qubes-way
        for vm in self.qc.values():
            if vm.name.startswith(VMPREFIX):
                self._remove_vm_qubes(vm)

        # now remove what was only in libvirt
        for dom in self.conn.listAllDomains():
            if dom.name().startswith(VMPREFIX):
                self._remove_vm_libvirt(dom)

        # finally remove anything that is left on disk
        vmnames = set()
        for dirspec in (
                'qubes_appvms_dir',
                'qubes_servicevms_dir',
                'qubes_templates_dir'):
            dirpath = os.path.join(qubes.qubes.system_path['qubes_base_dir'],
                qubes.qubes.system_path[dirspec])
            for name in os.listdir(dirpath):
                if name.startswith(VMPREFIX):
                    vmnames.add(name)
        for vmname in vmnames:
            self._remove_vm_disk(vmname)



class BackupTestsMixin(SystemTestsMixin):
    def setUp(self):
        super(BackupTestsMixin, self).setUp()
        self.error_detected = multiprocessing.Queue()
        self.verbose = False

        if self.verbose:
            print >>sys.stderr, "-> Creating backupvm"

        self.backupvm = self.qc.add_new_vm("QubesAppVm",
            name=self.make_vm_name('backupvm'),
            template=self.qc.get_default_template())
        self.backupvm.create_on_disk(verbose=self.verbose)

        self.backupdir = os.path.join(os.environ["HOME"], "test-backup")
        if os.path.exists(self.backupdir):
            shutil.rmtree(self.backupdir)
        os.mkdir(self.backupdir)


    def tearDown(self):
        super(BackupTestsMixin, self).tearDown()
        shutil.rmtree(self.backupdir)


    def print_progress(self, progress):
        if self.verbose:
            print >> sys.stderr, "\r-> Backing up files: {0}%...".format(progress)


    def error_callback(self, message):
        self.error_detected.put(message)
        if self.verbose:
            print >> sys.stderr, "ERROR: {0}".format(message)


    def print_callback(self, msg):
        if self.verbose:
            print msg


    def fill_image(self, path, size=None, sparse=False):
        block_size = 4096

        if self.verbose:
            print >>sys.stderr, "-> Filling %s" % path
        f = open(path, 'w+')
        if size is None:
            f.seek(0, 2)
            size = f.tell()
        f.seek(0)

        for block_num in xrange(size/block_size):
            f.write('a' * block_size)
            if sparse:
                f.seek(block_size, 1)

        f.close()


    # NOTE: this was create_basic_vms
    def create_backup_vms(self):
        template=self.qc.get_default_template()

        vms = []
        vmname = self.make_vm_name('test1')
        if self.verbose:
            print >>sys.stderr, "-> Creating %s" % vmname
        testvm1 = self.qc.add_new_vm('QubesAppVm',
            name=vmname, template=template)
        testvm1.create_on_disk(verbose=self.verbose)
        vms.append(testvm1)
        self.fill_image(testvm1.private_img, 100*1024*1024)

        vmname = self.make_vm_name('testhvm1')
        if self.verbose:
            print >>sys.stderr, "-> Creating %s" % vmname
        testvm2 = self.qc.add_new_vm('QubesHVm', name=vmname)
        testvm2.create_on_disk(verbose=self.verbose)
        self.fill_image(testvm2.root_img, 1024*1024*1024, True)
        vms.append(testvm2)

        return vms


    def make_backup(self, vms, prepare_kwargs=dict(), do_kwargs=dict(),
            target=None):
        # XXX: bakup_prepare and backup_do don't support host_collection
        self.qc.unlock_db()
        if target is None:
            target = self.backupdir
        try:
            files_to_backup = \
                qubes.backup.backup_prepare(vms,
                                      print_callback=self.print_callback,
                                      **prepare_kwargs)
        except QubesException as e:
            self.fail("QubesException during backup_prepare: %s" % str(e))

        try:
            qubes.backup.backup_do(target, files_to_backup, "qubes",
                             progress_callback=self.print_progress,
                             **do_kwargs)
        except QubesException as e:
            self.fail("QubesException during backup_do: %s" % str(e))

        self.qc.lock_db_for_writing()
        self.qc.load()


    def restore_backup(self, source=None, appvm=None):
        if source is None:
            backupfile = os.path.join(self.backupdir,
                                      sorted(os.listdir(self.backupdir))[-1])
        else:
            backupfile = source

        with self.assertNotRaises(qubes.qubes.QubesException):
            backup_info = qubes.backup.backup_restore_prepare(
                backupfile, "qubes",
                host_collection=self.qc,
                print_callback=self.print_callback,
                appvm=appvm)

        if self.verbose:
            qubes.backup.backup_restore_print_summary(backup_info)

        with self.assertNotRaises(qubes.qubes.QubesException):
            qubes.backup.backup_restore_do(
                backup_info,
                host_collection=self.qc,
                print_callback=self.print_callback if self.verbose else None,
                error_callback=self.error_callback)

        errors = []
        while not self.error_detected.empty():
            errors.append(self.error_detected.get())
        self.assertTrue(len(errors) == 0,
                         "Error(s) detected during backup_restore_do: %s" %
                         '\n'.join(errors))
        if not appvm:
            os.unlink(backupfile)


    def create_sparse(self, path, size):
        f = open(path, "w")
        f.truncate(size)
        f.close()

# vim: ts=4 sw=4 et
