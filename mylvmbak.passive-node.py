#!/bin/env python
"""Mylvmbak

This script just append <Mysql not running> message 
so that a logchecker knows script has run properly
"""

import sys, os, logging
import ConfigParser

cfg = {}

# Check if running as root
if os.geteuid() != 0:
  print "This script must be run as root. Exiting..."
  sys.exit(1)

# Get Script's filename
#sBasename = os.path.abspath(__file__).split('/')[-1]
sBasename = 'mylvmbak'

# Open config file
cfgFilePath = '/etc/'+sBasename+'.cfg'

cp = ConfigParser.ConfigParser()
try:
  cp.readfp(open(cfgFilePath))
except IOError,error:
  print "Failed opening config file, err:%s.Exiting..." % error
  sys.exit(1)

# Set default values
cfg['logFile']         = '/var/log/mylvmbak/mylvmbak.log'
cfg['passiveMsg']      = 'Mysql-not-running-Here so nothing to backup'

# Parse config file
for e in ['logFile','pidFile','passiveMsg']:
  try:
    cfg[e] = cp.get(sBasename,e)
  except ConfigParser.NoOptionError,error:
    pass

logging.basicConfig(filename=cfg['logFile'],level=logging.INFO,format='%(asctime)s %(name)-4s %(levelname)-4s: %(message)s',datefmt='%Y-%m-%d %H:%M:%S')

logging.info(cfg['passiveMsg'])

