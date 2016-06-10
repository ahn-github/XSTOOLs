#!/usr/bin/python
# -*- coding: utf-8 -*-
# **********************************************************************
#   This program is free software; you can redistribute it and/or
#   modify it under the terms of the GNU General Public License
#   as published by the Free Software Foundation; either version 2
#   of the License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA
#   02111-1307, USA.
#
#   (c)2012 - X Engineering Software Systems Corp. (www.xess.com)
# **********************************************************************

"""
Classes for types of XESS FPGA boards.
"""
import os
import time

from pubsub import pub

import xstools
from xstools.flashdev import W25X
from xstools.picmicro import Pic18f14k50
from xstools.ramdev import Sdram_8MB, Sdram_32MB
from xstools.xilfpga import Xc3s50avq100, Xc3s200avq100, Xc6slx25ftg256, \
    Xc6slx9ftg256
from xstools.xsdutio import XsDutIo
from xstools.xserror import XsMajorError, XsError
from xstools.xsjtag import XsJtag
from xstools.xsusb import XsUsb, XsMinorError


class XsBoard:
    """Class object for a generic XESS FPGA board."""
    @classmethod
    def get_xsboard(cls, xsusb_id=0, xsboard_name=''):
        """Detect which type of XESS board is connected to a USB port."""
        
        if xsusb_id is None:
            return None
        
        # All possible board types. XulaNoJtag must be first because it is
        # impossible to query what type of FPGA it has.
        board_classes = (XulaOldFmw, Xula50, Xula200, Xula2lx25, Xula2lx9,
                         XulaNoJtag)

        for c in board_classes:
            if xsboard_name.lower() == c.name.lower():
                return c(xsusb_id)
        
        for c in board_classes:
            try:
                xsboard = c(xsusb_id)
                if xsboard.is_connected():
                    return xsboard
            except XsError:
                pass

        return None

_PHASE = 'Progress.Phase'
        

class XulaMicro(XsBoard):
    """
    Class for XuLA-type boards that only includes methods of the microcontroller
    interface.
    """
    def __init__(self, xsusb_id=0):
        # Create a USB interface for the board object.
        self.xsusb = XsUsb(xsusb_id)
        # Now attach a JTAG interface to the USB interface.
        self.xsjtag = XsJtag(self.xsusb)
        # Instantiate microcontroller. (Override this in subclass if a different
        # uC is used.)
        self.micro = Pic18f14k50(xsusb=self.xsusb)

    def reset(self):
        """Reset the XESS board."""
        self.xsusb.reset()

    def get_board_info(self):
        """
        Return version information stored in the XESS board as a dictionary.
        """
        try:
            info = self.xsusb.get_info()
        except XsError:
            try:
                self.reset()
                info = self.xsusb.get_info()
            except XsError:
                raise XsMajorError('Unable to get XESS board information.')
        if sum(info) & 0xff != 0:
            # Checksum failure.
            raise XsMinorError('XESS board information is corrupted.')
        board_info = {}
        board_info['ID'] = '%02x%02x' % (info[1], info[2])
        board_info['VERSION'] = '%d.%d' % (info[3], info[4])
        # Description is 0-terminated string
        desc = info[5:]
        desc_len = desc.index(0)
        board_info['DESCRIPTION'] = desc[:desc_len].tostring()
        return board_info
        
    def get_board_fmw_version(self):
        """Return version number of XuLA microcontroller firmware as a float."""
        board_info = self.get_board_info()
        return float(board_info['VERSION'])
        
    def is_connected(self):
        """Return true if the board is connected to a USB port."""
        try:
            version = self.get_board_fmw_version()
        except XsError:
            return False

        if version < 1.2:
            return False
        elif hasattr(self, 'fpga'):
            return self.fpga.is_connected()
        return False
        
    def get_xsusb_id(self):
        """Return the USB port number the board is connected to."""
        return self.xsusb.get_xsusb_id()

    def update_firmware(self, hexfile=None):
        """Re-flash microcontroller with new firmware from hex file."""

        pub.sendMessage(_PHASE, phase='Updating firmware')
        if hexfile is None:
            hexfile = self.firmware
        self.micro.enter_reflash_mode()
        self.micro.program(hexfile)
        self.micro.enter_user_mode()
        # uC flash sometimes enables auxiliary JTAG cable, so make sure it's
        # disabled.
        self.micro.disable_jtag_cable()
        pub.sendMessage(_PHASE, phase='Firmware update done')

    def verify_firmware(self, hexfile):
        """
        Compare the microcontroller firmware to the contents of a hex file.
        """
        
        pub.sendMessage(_PHASE, phase='Verifying firmware')
        if hexfile is None:
            hexfile = self.firmware
        self.micro.enter_reflash_mode()
        self.micro.verify(hexfile)
        self.micro.enter_user_mode()
        pub.sendMessage(_PHASE, phase='Firmware verification done')
        
    def set_aux_jtag_flag(self, flag):
        if not flag:
            self.micro.disable_jtag_cable()
            return False
        else:
            self.micro.enable_jtag_cable()
            return True
        
    def get_aux_jtag_flag(self):
        return self.micro.get_jtag_cable_flag() != 0
        
    def toggle_aux_jtag_flag(self):
        return self.set_aux_jtag_flag(not self.get_aux_jtag_flag())
        
    def set_flash_flag(self, flag):
        if not flag:
            self.micro.disable_cfg_flash()
            return False
        else:
            self.micro.enable_cfg_flash()
            return True
        
    def get_flash_flag(self):
        return self.micro.get_cfg_flash_flag() != 0
        
    def toggle_flash_flag(self):
        return self.set_flash_flag(not self.get_flash_flag())

        
class XulaBase(XulaMicro):
    """Base class for all XuLA-type boards."""
    # IDs for HostIo modules.
    _TEST_MODULE_ID = 0x01  # Board diagnostic module ID.
    _CFG_FLASH_MODULE_ID = 0x02  # Configuration flash programming module ID.
    _SDRAM_MODULE_ID = 0x03  # SDRAM R/W module ID.
    
    def __init__(self, xsusb_id=0):
        XulaMicro.__init__(self, xsusb_id)
        # Create a few attributes to indicate the presence of these devices on
        # the board.
        self.cfg_flash = None  # The value doesn't matter. Just its existence.
        self.sdram = None

    def configure(self, bitstream, silent=False):
        """Configure the FPGA on the board with a bitstream."""

        pub.sendMessage(_PHASE, phase='Downloading bitstream')
        # Clear any configuration already in the FPGA.
        self.xsusb.set_prog(1)
        self.xsusb.set_prog(0)
        self.xsusb.set_prog(1)
        time.sleep(0.03)  # Wait for FPGA to clear.
        # Configure the FPGA with the bitstream.
        self.fpga.configure(bitstream)
        pub.sendMessage(_PHASE, phase='Download complete')
        
    def do_self_test(self, test_bitstream=None):
        """Load the FPGA with a bitstream to test the board."""

        BASE_SIGNATURE = 0xA50000A5
        SELF_TEST_SIGNATURE = BASE_SIGNATURE | (1<<8)
        (TEST_START, TEST_WRITE, TEST_READ, TEST_DONE) = range(0,4)
        
        if test_bitstream is None:
            test_bitstream = self.test_bitstream
        pub.sendMessage(_PHASE,
                           phase='Downloading diagostic bitstream')
        self.configure(test_bitstream, silent=True)
        # Create a channel to query the results of the board test.
        dut = XsDutIo(xsjtag=self.xsjtag, module_id=self._TEST_MODULE_ID,
                      dut_output_widths=[2,1,32], dut_input_widths=1)
        # Assert and release the reset for the testing circuit.
        dut.write(1)
        dut.write(0)
        pub.sendMessage(_PHASE, phase='Writing SDRAM')
        prev_progress = TEST_START
        while True:
            [progress, failed, signature] = dut.read()
            if signature.unsigned != SELF_TEST_SIGNATURE:
                msg = 'FPGA is not configured with diagnostic bitstream.'
                raise XsMajorError(self.name + msg)
            if progress.unsigned != prev_progress:
                if progress.unsigned == TEST_READ:
                    pub.sendMessage(_PHASE, phase='Reading SDRAM')
                if failed.unsigned == 1:
                    pub.sendMessage(_PHASE, phase='Test Done')
                    raise XsMinorError(self.name + ' failed diagnostic test.')
                elif progress.unsigned == TEST_DONE:
                    pub.sendMessage(_PHASE, phase='Test Done')
                    return  # Test passed!
            prev_progress = progress.unsigned
        
    def read_cfg_flash(self, bottom, top):
        pub.sendMessage(_PHASE, phase='Configuring FPGA for reading '
                                      'configuration flash')
        self.configure(self.cfg_flash_bitstream, silent=True)
        pub.sendMessage(_PHASE, phase='Reading configuration flash')
        self.cfg_flash = self.create_cfg_flash()
        hex_data = self.cfg_flash.read(bottom, top)
        pub.sendMessage(_PHASE, phase='Configuration flash read done')
        return hex_data
        
    def write_cfg_flash(self, hexfile, bottom=None, top=None):
        pub.sendMessage(_PHASE, phase='Configuring FPGA for writing '
                                         'configuration flash')
        self.configure(self.cfg_flash_bitstream, silent=True)
        pub.sendMessage(_PHASE, phase='Erasing configuration flash')
        self.cfg_flash = self.create_cfg_flash()
        self.cfg_flash.erase()
        pub.sendMessage(_PHASE, phase='Writing configuration flash')
        self.cfg_flash.write(hexfile, bottom, top)
        pub.sendMessage(_PHASE, phase='Configuration flash write done')
        
    def erase_cfg_flash(self, bottom, top):
        pub.sendMessage(_PHASE, phase='Configuring FPGA for erasing '
                                         'configuration flash')
        self.configure(self.cfg_flash_bitstream, silent=True)
        pub.sendMessage(_PHASE, phase='Erasing configuration flash')
        self.cfg_flash = self.create_cfg_flash()
        self.cfg_flash.erase()
        pub.sendMessage(_PHASE, phase='Configuration flash erase done')
        
    def read_sdram(self, bottom, top):
        pub.sendMessage(_PHASE, phase='Configuring FPGA for reading SDRAM')
        self.configure(self.sdram_bitstream, silent=True)
        pub.sendMessage(_PHASE, phase='Reading SDRAM')
        self.sdram = self.create_sdram()
        hex_data = self.sdram.read(bottom, top)
        pub.sendMessage(_PHASE, phase='SDRAM read done')
        return hex_data
    
    def write_sdram(self, hexfile, bottom=None, top=None):
        pub.sendMessage(_PHASE, phase='Configuring FPGA for writing SDRAM')
        self.configure(self.sdram_bitstream, silent=True)
        pub.sendMessage(_PHASE, phase='Writing SDRAM')
        self.sdram = self.create_sdram()
        self.sdram.write(hexfile, bottom, top)
        pub.sendMessage(_PHASE, phase='SDRAM write done')
        
    def erase_sdram(self, bottom, top):
        pub.sendMessage(_PHASE, phase='Configuring FPGA for erasing SDRAM')
        self.configure(self.sdram_bitstream, silent=True)
        pub.sendMessage(_PHASE, phase='Erasing SDRAM')
        self.sdram = self.create_sdram()
        self.sdram.erase(bottom, top)
        pub.sendMessage(_PHASE, phase='SDRAM erase done')
        return


class Xula(XulaBase):
    """Class for a generic XuLA board."""
    name = 'XuLA'
    dir = os.path.join(xstools.install_dir, 'xula')
    firmware = os.path.join(dir, 'XuLA_jtag.hex')
    
    def __init__(self, xsusb_id=0):
        XulaBase.__init__(self, xsusb_id)
        
    def create_cfg_flash(self):
        """Create the serial configuration flash for this board."""
        return W25X(module_id=self._CFG_FLASH_MODULE_ID, xsjtag=self.xsjtag)
        
    def read_cfg_flash(self, bottom, top):
        cfg_flash_flag = self.micro.get_cfg_flash_flag()
        self.micro.enable_cfg_flash()
        data = XulaBase.read_cfg_flash(self,bottom, top)
        self.micro.set_cfg_flash_flag(cfg_flash_flag)
        return data
        
    def write_cfg_flash(self, hexfile, bottom=None, top=None):
        cfg_flash_flag = self.micro.get_cfg_flash_flag()
        self.micro.enable_cfg_flash()
        XulaBase.write_cfg_flash(self, hexfile, bottom, top)
        self.micro.set_cfg_flash_flag(cfg_flash_flag)
        
    def erase_cfg_flash(self, bottom=None, top=None):
        cfg_flash_flag = self.micro.get_cfg_flash_flag()
        self.micro.enable_cfg_flash()
        XulaBase.erase_cfg_flash(self, bottom, top)
        self.micro.set_cfg_flash_flag(cfg_flash_flag)
        
    def create_sdram(self):
        """Create the SDRAM for this board."""
        return Sdram_8MB(module_id=self._SDRAM_MODULE_ID, xsjtag=self.xsjtag)


class Xula50(Xula):
    """Class for a XuLA board with an XC3S50A FPGA."""
    name = Xula.name + '-50'
    test_bitstream = os.path.join(Xula.dir, 'test_board_jtag_50.bit')
    cfg_flash_bitstream = os.path.join(Xula.dir, 'fintf_jtag_50.bit')
    sdram_bitstream = os.path.join(Xula.dir, 'ramintfc_jtag_50.bit')

    def __init__(self, xsusb_id=0):
        Xula.__init__(self, xsusb_id)
        self.fpga = Xc3s50avq100(self.xsjtag)


class Xula200(Xula):
    """Class for a XuLA board with an XC3S200A FPGA."""
    name = Xula.name + '-200'
    test_bitstream = os.path.join(Xula.dir, 'test_board_jtag_200.bit')
    cfg_flash_bitstream = os.path.join(Xula.dir, 'fintf_jtag_200.bit')
    sdram_bitstream = os.path.join(Xula.dir, 'ramintfc_jtag_200.bit')

    def __init__(self, xsusb_id=0):
        Xula.__init__(self, xsusb_id)
        self.fpga = Xc3s200avq100(self.xsjtag)


class Xula2(XulaBase):
    """Class for a generic XuLA2 board."""
    name = 'XuLA2'
    dir = os.path.join(xstools.install_dir, 'xula2')
    firmware = os.path.join(dir, 'XuLA_jtag.hex')
    
    def __init__(self, xsusb_id=0):
        XulaBase.__init__(self, xsusb_id)
        
    def create_cfg_flash(self):
        """Create the serial configuration flash for this board."""
        return W25X(module_id=self._CFG_FLASH_MODULE_ID, xsjtag=self.xsjtag)
        
    def create_sdram(self):
        """Create the SDRAM for this board."""
        return Sdram_32MB(module_id=self._SDRAM_MODULE_ID, xsjtag=self.xsjtag)
        
    def set_flash_flag(self, flag):
        return True
        
    def get_flash_flag(self):
        return True  # Flash is always enabled for XuLA2.
        
    def toggle_flash_flag(self):
        return self.get_flash_flag()  # Flash enable doesn't change on XuLA2.


class Xula2lx25(Xula2):
    """Class for a XuLA2 board with an XC6SLX25 FPGA."""
    name = Xula2.name + '-LX25'
    test_bitstream = os.path.join(Xula2.dir, 'test_board_jtag_lx25.bit')
    cfg_flash_bitstream = os.path.join(Xula2.dir, 'fintf_jtag_lx25.bit')
    sdram_bitstream = os.path.join(Xula2.dir, 'ramintfc_jtag_lx25.bit')
    
    def __init__(self, xsusb_id=0):
        Xula2.__init__(self, xsusb_id)
        self.fpga = Xc6slx25ftg256(self.xsjtag)


class Xula2lx9(Xula2):
    """Class for a XuLA2 board with an XC6SLX9 FPGA."""
    name = Xula2.name + '-LX9'
    test_bitstream = os.path.join(Xula2.dir, 'test_board_jtag_lx9.bit')
    cfg_flash_bitstream = os.path.join(Xula2.dir, 'fintf_jtag_lx9.bit')
    sdram_bitstream = os.path.join(Xula2.dir, 'ramintfc_jtag_lx9.bit')
    
    def __init__(self, xsusb_id=0):
        Xula2.__init__(self, xsusb_id)
        self.fpga = Xc6slx9ftg256(self.xsjtag)

    
class XulaOldFmw(XulaMicro):
    """XuLA with old firmware so the JTAG port is not usable."""
    name = 'XuLA UNKNOWN'

    def __init__(self, xsusb_id=0):
        XulaMicro.__init__(self, xsusb_id)
        
    def is_connected(self):
        """Return true if the board is connected to a USB port."""
        try:
            version = self.get_board_fmw_version()
        except XsError:
            return False

        # True if the firmware is too old to query the JTAG port.
        return version < 1.2

    
class XulaNoJtag(XulaMicro):
    """XuLA with disabled JTAG so only the microcontroller is visible."""
    
    name = 'XuLA UNKNOWN'
    
    def __init__(self, xsusb_id=0):
        XulaMicro.__init__(self, xsusb_id)
        
    def is_connected(self):
        """Return true if the board is connected to a USB port."""
        try:
            version = self.get_board_fmw_version()
        except XsError:
            return False
            
        # If the firmware is new, assume the FPGA IDCODE can't be queried
        # because the JTAG is deactivated.
        return version >= 1.2


# if __name__ == '__main__':
#     xula = Xula2lx25(0)
#     board_info = xula.get_board_info()
#     print(repr(board_info))
#
#     xula.do_self_test()
#
#     wr_data = IntelHex()
#     for i in range(0x100):
#         wr_data[i] = (i*75) & 0xff
#     wr_data.write_hex_file(sys.stdout)
#
#     print('Write flash...')
#     xula.write_cfg_flash(wr_data, 0, 0x100)
#
#     print('Read flash...')
#     rd_data = xula.read_cfg_flash(0, 0x100)
#
#     rd_data.write_hex_file(sys.stdout)
