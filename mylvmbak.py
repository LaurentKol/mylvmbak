#!/bin/env python
"""Mylvmbak

Mysql backup script using LVM snapshot

Tested on Mysql Server v5.1 and v5.5

This script do the following:
1. Lock mysql DB
2. Snapshot its Logical Volume
3. Unlock the DB
4. Start a mysqld backup instance
5. Mysqldump all the databases to a sql.gz file in a backup location
"""
__author__ = "Laurent Kolakofsky (laurent.kol@gmail.com)"
__version__ = "$Revision: 0.6.4 $"
__license__ = "Python"

import _mysql
import time, datetime
import sys, os, re, pwd, grp
import shlex, subprocess, commands
import logging
import getopt, ConfigParser
from socket import gethostname

# Declaring core functions
###########################
def usage():
  print "Usage: %s [-h|-p|-d]\n\
Create a mysql backup using LVM Snapshot.\n\
For more details look at config file: /etc/%s.cfg\n\
         -h,--help                     Print usage\n\
         -d,--debug                    Override log level to debug\n\
         -p,--passive                  Passive/Pretend mode, do nothing except update log, i.e. for HA cluster\n\
  " % (sBasename,sBasename)

def openAndWait(cmdBash,timeout=30,ignoreReturncode=False):
  log.debug("Cmd: "+cmdBash)
  execTime = '>%s' % timeout
  timeZero = time.time()
  try:
    p = subprocess.Popen(cmdBash, shell=True, env={"PATH": "/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin:/root/bin"}, \
                         close_fds=False,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    (pid, stdout, stderr) = (p.pid, p.stdout.read().strip('\n'), p.stderr.read().strip('\n'))
    for i in range(0,int(timeout)):
      if p.poll() != None:
        execTime = time.time() - timeZero
        break
      time.sleep(1)
    log.debug("pid: %s" % pid)
    log.debug("returnCode: %s" % p.poll())
    log.debug("execTime: %s" % execTime)
    log.debug("stdout: %s" % stdout)
    log.debug("stderr: %s" % stderr)
    if p.returncode != 0 and not ignoreReturncode:
      log.error("Return code of '%s' was %s, stderr: %s" % (cmdBash,p.returncode,stderr))
    return {'pid':pid,'returnCode':p.poll(),'stdout':stdout,'stderr':stderr,'execTime':execTime}
  except TypeError, msg:
    log.error("Failed to run: %s\nError: %s\nSkipping...\n" % (cmdBash, msg))
    return {'process':None,'execTime':None}

def openAndDetach(cmdBash):
  log.debug("Cmd: "+cmdBash)
  try:
    p = subprocess.Popen(cmdBash, shell=True, env={"PATH": "/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin:/root/bin"}, \
                         close_fds=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,universal_newlines=True)
    #log.debug("pid: %s\nreturnCode: %s\nstdout: %s\nstderr: %s\n" % (pid, returncode, stdout, stderr))
    pid = p.pid
    log.debug("pid: %s" % pid)
    return {'pid':pid}
  except TypeError, msg:
    log.error("Failed to run: %s\nError: %s\nSkipping...\n" % (cmdBash, msg))
    return False

def CreateDirIfDontExist(dirPath,uid = -1,gid = -1):
  if not os.path.exists(dirPath):
    try:
      os.makedirs(dirPath)
    except:
      print "Failed creating directory '%s', exiting"
      sys.exit(1)
  try:
    if isinstance(uid,str): uid = pwd.getpwnam(uid).pw_uid
    if isinstance(gid,str): gid = grp.getgrnam(gid).gr_gid
    os.chown(dirPath,uid,gid)
  except KeyError, msg:
    print "Failed chown on '%s', user:%s, group=%s : %s" % (dirPath,uid,gid,msg)
    sys.exit(1)

def removeOldBackup(mysqldumpDir,daysHistoryToKeep):
  nbFileRemoved = 0
  r = openAndWait("find %s -type f -mtime +%s -exec rm -fv {} \; " % (mysqldumpDir,daysHistoryToKeep))
  for line in r['stdout'].splitlines():
    if re.match("^removed.*",line):
      nbFileRemoved += 1
    if re.match(".*denied$",line):
      log.warn("Error deleting file (%s)" % line)
  log.info("%s files removed from %s" % (nbFileRemoved,mysqldumpDir))

def ListDB(myHost,myCnf,mySocket=''):
  dbs = []
  try:
    if len(mySocket) > 0:
      conn = _mysql.connect(host=myHost,read_default_file=myCnf,unix_socket=mySocket)
    else:
      conn = _mysql.connect(host=myHost,read_default_file=myCnf)
    conn.query("SHOW DATABASES;")
  except :
    log.critical("Failed to connect to DB (%s), exiting..." % sys.exc_info()[1])
    sys.exit(1)
  r = conn.store_result()
  dbsBuff = r.fetch_row(maxrows=0,how=1)
  for db in dbsBuff:
    dbs.append(db['Database'])
  conn.close()
  return dbs

class mysqlVarQuery:
  'Mysql Server variable collector'
  def __init__(self,myHost,myCnf,mySocket=''):
    self.host = myHost
    self.sock = mySocket
    self.cnf = myCnf
    self.conn = None
  def connect(self):
    try:
      if len(self.sock) > 0:
        self.conn = _mysql.connect(host=self.host,read_default_file=self.cnf,unix_socket=self.sock)
      else:
        self.conn = _mysql.connect(host=self.host,read_default_file=self.cnf,unix_socket=self.sock)
      return True
    except:
      log.critical("Failed to connect to running mysql, error:%s, exiting..." % sys.exc_info()[1])
      print "Error: %s\nExiting..." % sys.exc_info()[1]
      sys.exit(1)
  def get(self,mysqlVarName):
    self.conn.query("SHOW VARIABLES LIKE '%s';" % mysqlVarName )
    r = self.conn.store_result()
    varBuff = r.fetch_row(maxrows=0,how=1)
    if len(varBuff) > 0:
      log.debug('%s: %s' % (mysqlVarName,varBuff[0]['Value']))
      return varBuff[0]['Value']
    else:
      return None
  def disconnect(self):
    self.conn.close()

def createMysqldumpFilePath(dbName):
  return cfg['mysqldumpDir']+"/"+var['hostname']+"-"+datetime.datetime.now().strftime("%Y-%m-%d-%Hh%M")+"-"+dbName+".sql.gz"

def killBackupMysqld(timeout):
  openAndWait("for pid in $( ps ax | grep '\-\-pid\-file=%s' | gawk -F' ' '{ print $1 }') ; do kill $pid ; done" % cfg['mysqldBackupPidFile'])
  for i in range(timeout):
    shutdownDelay = i
    r = openAndWait('ps ax | grep "\-\-pid\-file=%s"' % cfg['mysqldBackupPidFile'],ignoreReturncode=True)
    if not r['returnCode'] == 0:
      log.info("Mysql Backup Server took %ssec to go down..." % shutdownDelay)
      time.sleep(3) # Might still have files open that block umounting
      break
    time.sleep(1)
  if shutdownDelay > timeout:
    log.warn("Can't terminate Mysql Backup Server !" % shutdownDelay)

def umountLVsnapshot():
  mtab = open('/etc/mtab','r')
  for l in mtab.readlines():
    if l.split()[1].strip('/') == cfg['lvmSnapshotMountDir'].strip('/'):
      #log.warn("%s is still mounted, last execution didn't terminate properly :(" % cfg['lvmSnapshotMountDir'])
      log.info("%s is mounted" % cfg['lvmSnapshotMountDir'])
      r = openAndWait("sudo umount %s" % cfg['lvmSnapshotMountDir'],ignoreReturncode=True)
      if r['returnCode'] == 0:
        log.info("%s was umounted successfully :)" % cfg['lvmSnapshotMountDir'])
      else:
        log.error("%s failed to umount" % cfg['lvmSnapshotMountDir'])
  mtab.close()

def removeLVsnapshot(printError):
  r = openAndWait("lvs | grep '%s.*swi...'" % (cfg['mysqldBackupLVName']),ignoreReturncode=True)
  if r['returnCode'] == 0:
    #log.warn("%s Snapshot LV still exist, last execution didn't terminate properly :(" % cfg['mysqldBackupLVName'])
    r = openAndWait("lvremove -f /dev/%s/%s" % (cfg['mysqlVg'], cfg['mysqldBackupLVName']), ignoreReturncode=True)
    if r['returnCode'] == 0:
      log.info("%s was removed successfully :)" % cfg['mysqldBackupLVName'])
    elif printError == True:
      log.error("Removing %s seems to have failed" % cfg['mysqldBackupLVName'])

def cleanupFromCrash():
  if os.path.exists(cfg['mysqldBackupPidFile']):
    killBackupMysqld(180)
  umountLVsnapshot()
  removeLVsnapshot(False)

def waitUntilMysqlServerIsUp(myHost,myCnf,mySocket,pidFile,timeout):
  log.debug("Entering wait until Mysql Server is up")
  ts1 = time.time()
  for i in range(timeout):
    if i == timeout - 1:
      log.warning("Mysql Backup server might be up but couldnt connect after %ssec" % timeout)
    if not os.path.exists(pidFile):
      time.sleep(1)
      continue
    log.debug("Mysql Backup server has a pid file")
    try:
      if len(mySocket) > 0:
        log.debug("Trying to connect with %s %s %s" % (myHost,myCnf,mySocket))
        conn = _mysql.connect(host=myHost,read_default_file=myCnf,unix_socket=mySocket)
        conn = _mysql.connect(host=myHost,read_default_file=myCnf,unix_socket=mySocket)
      else:
        log.debug("Trying to connect with %s %s" % (myHost,myCnf))
        conn = _mysql.connect(host=myHost,read_default_file=myCnf)
      log.debug("Trying query...")
      conn.query("SHOW DATABASES;") # This should raise exception if server is down
      if not len(conn.store_result().fetch_row(maxrows=0,how=1)[0]['Database']) > 0:
        raise dbIsNotUp
      ts2 = time.time()
      log.info("Mysql Backup Server is up, waited %ssec" % (ts2-ts1))
      break
    except (error):
      time.sleep(1)

def loadConfig():
  # Open config file
  cfgFilePath = '/etc/'+sBasename+'.cfg'
  cp = ConfigParser.ConfigParser()
  try:
    cp.readfp(open(cfgFilePath))
  except IOError,error:
    print "Failed opening %s  err:%s.Exiting..." % (cfgFilePath,error)
    sys.exit(1)

  # Parse config file for mandatory element
  cfgMandatoryElement = ['logLevel','mysqlHostname','mysqlLv','mysqlVg','daysHistoryToKeep', \
                         'mysqldumpDir','minFreeVGsize','mysqlCheck','backupBinlog']
  try:
    for e in cfgMandatoryElement:
      cfg[e] = cp.get(sBasename,e)
  except ConfigParser.NoOptionError,error:
    print "Setting is missing in config file, err:%s.Exiting..." % error
    sys.exit(1)

  # Set default value for optional element
  cfg['mysqldOpt'],cfg['mysqldumpOpt'],cfg['myCnfCred'],cfg['mysqldOpt'],cfg['mysqldumpOpt'] = '','','','',''
  cfg['mysqlSocketFile'] = '/var/lib/mysql/mysql.sock'
  cfg['mysqldumpOwner']  = 'root'
  cfg['logFile']         = '/var/log/mylvmbak/mylvmbak.log'
  cfg['pidFile']         = '/var/run/mylvmbak/mylvmbak.pid'
  cfg['passiveMsg']      = 'Mysql-not-running-Here so nothing to backup'
  cfg['mysqldBackupErrorLogFile'] = '/var/log/mylvmbak/mysql-backup.err'
  cfg['mysqldBackupPidFile']      = '/var/run/mylvmbak/mysql-backup.pid'
  cfg['mysqldBackupSocketFile']   = '/var/lib/mylvmbak/mysql-backup.sock'
  cfg['mysqldBackupLVName']       = 'mysqlbackuplv'
  cfg['mysqldBackupLVMountbase']  = '/var/lib/mylvmbak'
  cfg['mysqldBackupInnodbBufferPoolSize']  = '128M'

  # Parse config file for optional element
  cfgOptionalElement = ['mysqlUser','mysqldDataDirFromLvRoot','mysqldBinlogDirFromLvRoot','mysqldInnodbDataDirFromLvRoot', \
                        'mysqldOpt','mysqldumpOpt','mysqldBin','mysqldumpBin','mysqlcheckBin','myCnfCred','mysqlSocketFile', \
                        'logFile','pidFile','mysqldBackupErrorLogFile','mysqldBackupPidFile','mysqldBackupSocketFile', \
                        'mysqldBackupLVName','mysqldBackupLVMountbase','passiveMsg','mysqldumpOwner','mysqldBackupInnodbBufferPoolSize']
  for e in cfgOptionalElement:
    try:
      cfg[e] = cp.get(sBasename,e)
    except ConfigParser.NoOptionError,error:
      pass
  
  # Set extra config var.
  cfg['lvmSnapshotMountDir'] = cfg['mysqldBackupLVMountbase']+'/'+cfg['mysqldBackupLVName'].rstrip('/')

  # Add extra mysqld option from config file
  var['mysqldExtraOpt'] = "%s " % cfg['mysqldOpt']


def checkRoot():
  if os.geteuid() != 0:
    print "This script must be run as root. Exiting..."
    sys.exit(1)

def initLogging():
  CreateDirIfDontExist('/'.join(cfg['logFile'].split('/')[0:-1]),'root','root')
  logLvls = {'debug': logging.DEBUG, 'info': logging.INFO, 'warning': logging.WARNING, 'error': logging.ERROR, 'critical': logging.CRITICAL}
  if not 'logLevel' in var:
    var['logLevel'] = cfg['logLevel']

  log = logging.getLogger(sBasename)
  log.setLevel(logging.DEBUG)
  # create file handler
  fh = logging.FileHandler(cfg['logFile'])
  fh.setLevel(logLvls[var['logLevel']])
  fh.setFormatter(logging.Formatter('%(asctime)s %(name)-4s %(levelname)-4s: %(message)s'))
  # create console handler
  ch = logging.StreamHandler()
  ch.setLevel(logging.CRITICAL)
  ch.setFormatter(logging.Formatter('%(message)s'))
  # add the handlers to logger
  log.addHandler(ch)
  log.addHandler(fh)
  return log

def passiveRun():
  log.info(cfg['passiveMsg'])

def findBinaryPath(binaryName,possiblePath=None,criticalError=True):
  # First check if possiblePath
  if isinstance(possiblePath,list):
    for e in possiblePath:
      p = '%s/%s' % (e.rstrip('/'),binaryName)
      if os.path.exists(p):
        return p
  elif isinstance(possiblePath,str):
    p = '%s/%s' % (possiblePath.rstrip('/'),binaryName)
    if os.path.exists(p):
      return p

  # Try to find path with 'which'
  r = openAndWait("which %s" % binaryName,ignoreReturncode=True)
  if r['returnCode'] == 0:
    return r['stdout']
  elif criticalError:
    log.critical("Couldn't locate %s binary, please set in config file." % binaryName)
    sys.exit(1)
  else:
    return None


def getSystemInfo():
  # Get all sorts of system informations
  var['hostname'] = gethostname()

  # Get mysql binaries if not set in config file
  if 'mysqldBin' not in cfg:
    cfg['mysqldBin'] = findBinaryPath('mysqld','/usr/libexec')
  if 'mysqldumpBin' not in cfg:
    cfg['mysqldumpBin'] = findBinaryPath('mysqldump')
  if 'mysqlcheckBin' not in cfg:
    cfg['mysqlcheckBin'] = findBinaryPath('mysqlcheck')

  # Get mysqlUser if not set in config file
  if 'mysqlUser' not in cfg:
    cfg['mysqlUser'] = openAndWait("ps -eo user,comm | grep mysqld$")['stdout'].split()[0]
    if len(cfg['mysqlUser']) < 1:
      log.critical("Failed to determine which user is running mysqld, set 'mysqlUser' in config file")
      sys.exit(1)

  # Gather required server variables by querying mysqld
  mvq = mysqlVarQuery(cfg['mysqlHostname'],cfg['myCnfCred'],cfg['mysqlSocketFile'])
  mvq.connect()
 
  # Get MysqldVersion
  var['mysqldVersion'] = {}
  (major,minor) = re.sub(r'^([0-9]+)\.([0-9]+).*',r'\1\n\2',mvq.get('version')).split()
  (var['mysqldVersion']['major'],var['mysqldVersion']['minor']) = int(major),int(minor)

  # Variables NON-Specific to Mysql Server Version
  var['mysqlvarDatadir']   = mvq.get('datadir').strip('/')
  var['mysqlvarBinlogdir'] = mvq.get('innodb_log_group_home_dir').strip('/')
  var['mysqlvarInnodbdir'] = mvq.get('innodb_data_home_dir').strip('/')
  
  var['myBinlog'] = mvq.get('log_bin')
  var['myBaseDir'] = mvq.get('basedir')

  var['myBakInnodbDataFilePath'] =  mvq.get('innodb_data_file_path')

  var['myBakInnodbAdditionalMemPoolSize'] = str(int(mvq.get('innodb_additional_mem_pool_size')) / 1024 / 1024)+'M'
  var['myBakInnodbLogFileSize'] = str(int(mvq.get('innodb_log_file_size')) / 1024 / 1024)+'M'
  var['myBakInnodbLogBufferSize'] = str(int(mvq.get('innodb_log_buffer_size')) / 1024 / 1024)+'M'

  # Variables Specific to Mysql Server Version
  if var['mysqldVersion']['major'] == 5 and var['mysqldVersion']['minor'] >= 1:
    var['myBakBinlogFormat'] = mvq.get('binlog_format')
    var['mysqldExtraOpt'] += ' --binlog-format=%s ' % var['myBakBinlogFormat']
  else:
    var['mysqldExtraOpt'] += ''
 
  mvq.disconnect()


# Starting execution
#####################
ts0 = time.time()
global cfg
global var
cfg = {}
var = {}

# Get Script's filename
sBasename = os.path.abspath(__file__).split('/')[-1]

# Parse command line args
try:                                
  opts, args = getopt.getopt(sys.argv[1:], "hpd", ["help", "passive","debug"])
except getopt.GetoptError:          
  usage()                          
  sys.exit(2)

for opt, arg in opts:
  if opt in ('-d','--debug'):
    var['logLevel'] = 'debug'
  elif opt in ('-h','--help'):
    usage()
    sys.exit(0)
  elif opt in ('-p','--passive'):
    checkRoot()
    loadConfig()
    log = initLogging()
    passiveRun()
    sys.exit(0)

checkRoot()
loadConfig()
log = initLogging()
getSystemInfo()

# Check if user mysqldumpOwner exist
try:
  pwd.getpwnam(cfg['mysqldumpOwner'])
except KeyError:
  log.critical("User %s doesn't exist on this system, edit config file" % cfg['mysqldumpOwner'])
  sys.exit(1)

# Create script directories if they don't already exist
'/'.join(cfg['lvmSnapshotMountDir'].split('/')[0:-1])
CreateDirIfDontExist('/'.join(cfg['pidFile'].split('/')[0:-1]),cfg['mysqlUser'],'root')
CreateDirIfDontExist('/'.join(cfg['mysqldBackupErrorLogFile'].split('/')[0:-1]),cfg['mysqlUser'],'root')
CreateDirIfDontExist('/'.join(cfg['mysqldBackupPidFile'].split('/')[0:-1]),cfg['mysqlUser'],'root')
CreateDirIfDontExist('/'.join(cfg['mysqldBackupSocketFile'].split('/')[0:-1]),cfg['mysqlUser'],'root')
CreateDirIfDontExist(cfg['lvmSnapshotMountDir'],cfg['mysqlUser'],'root')
CreateDirIfDontExist(cfg['mysqldumpDir'])

# Remove backup older than DaysHistoryToKeep
removeOldBackup(cfg['mysqldumpDir'],cfg['daysHistoryToKeep'])

# Kill Backup Mysqld in case it's mysteriously still running
if os.path.exists(cfg['mysqldBackupPidFile']):
  killBackupMysqld(60)

# Umount LVM snapshot, in case last execution mysteriously failed
umountLVsnapshot()

# Remove LVM snapshot, in case last execution mysteriously failed
removeLVsnapshot(False)

# Get DB LV Size
dblvSize = openAndWait("lvdisplay -c /dev/%s/%s | gawk -F: '{ print $8 }'" % (cfg['mysqlVg'], cfg['mysqlLv']))['stdout']
log.debug("LV %s size is %s extends" % (cfg['mysqlLv'],dblvSize))

# Get DB VG Free space
dbvgFree = openAndWait("vgdisplay -c %s | gawk -F: '{ print $16 }'" % (cfg['mysqlVg']))['stdout']
log.debug("VG %s has %s extends free" % (cfg['mysqlVg'],dbvgFree))

# Get DB VG : Size of 1 PE in Kb, Total Number of PE of this vg, Free PE for this VG
try:
  peSizeInKb,dbvgSizeInPE,dbvgFreePE = openAndWait("vgdisplay -c %s | gawk -F: '{ print $13\"!@#\"$14\"!@#\"$16 }'" % (cfg['mysqlVg']))['stdout'].split('!@#')
except ValueError,error:
  log.critical("Couldn't find VolumeGroup: %s, exiting..." % cfg['mysqlVg'])
  sys.exit(1)
try:
  unitMinFreeVG,valueMinFreeVG = cfg['minFreeVGsize'][-1].lower(),int(cfg['minFreeVGsize'][0:-1])
except:
  log.critical("'minFreeVGsize' wrongly set in cfg file ! exiting ...")
  sys.exit(1)

# Calculate minmal free PE (physical extents) required
if unitMinFreeVG == '%':
  minFreeVGinPE = (valueMinFreeVG * int(dbvgSizeInPE)) / 100
elif unitMinFreeVG == 'm':
  minFreeVGinPE = (int(valueMinFreeVG) / int(peSizeInKb)) * 1024

# Check if VG has enough free PE (physical extents)
if int(minFreeVGinPE) < int(dbvgFreePE):
  log.info("VG has %s free PE, minimum required is %s, continuing ..." % (dbvgFreePE,minFreeVGinPE))
else:
  log.critical("VG only have %s extents free, minimum required is %s, exiting..." % (dbvgFreePE,minFreeVGinPE))
  sys.exit(1)

# Lock & Flush Mysql Server
log.info("Locking & Flushing DB's log")
ts1 = time.time()
if len(cfg['mysqlSocketFile']) > 0:
  conn = _mysql.connect(host=cfg['mysqlHostname'], read_default_file=cfg['myCnfCred'],unix_socket=cfg['mysqlSocketFile'])
else:
  conn = _mysql.connect(host=cfg['mysqlHostname'], read_default_file=cfg['myCnfCred'])

conn.query("FLUSH TABLES WITH READ LOCK;")
conn.query("FLUSH LOGS;")
r = 'empty'
r = conn.store_result()
if not r == None:  log.error('Failed to lock DB !!!')

# Create LVM Snapshot
r = openAndWait("lvcreate --snapshot --extents %s --name=%s /dev/%s/%s" % (dbvgFreePE, cfg['mysqldBackupLVName'], cfg['mysqlVg'], cfg['mysqlLv']),10)
if r['returnCode'] == 0:
  log.info("Snapshot LV %s was created successfully" % cfg['mysqldBackupLVName'])
else:
  log.critical("Creating Snapshot LV %s failed ! Exiting ..." % cfg['mysqldBackupLVName'])
  cleanupFromCrash()
  sys.exit(1)

# Unlock Database
conn.close()
ts2 = time.time()
lockDuration = ts2 - ts1
log.info("DB is unlocked, it was locked for %s second(s)" % lockDuration)

# Mount LVM Snapshot
openAndWait("mount /dev/%s/%s %s" % (cfg['mysqlVg'], cfg['mysqldBackupLVName'], cfg['lvmSnapshotMountDir']))

# Change permission for backupmysqlduser
openAndWait("chown -R %s:%s %s" %  (cfg['mysqlUser'], cfg['mysqlUser'], cfg['lvmSnapshotMountDir']))

# Archive Mysql Bin-log
if var['myBinlog'].lower() == 'on' and cfg['backupBinlog'].lower == 'true':
  mysqlBinlogTarPath = cfg['mysqldumpDir']+'/'+var['hostname']+'-'+datetime.datetime.now().strftime("%Y-%m-%d-%Hh%M")+"-binlog.tar.gz"
  r = openAndWait("tar cvzf %s %s" % (mysqlBinlogTarPath,cfg['lvmSnapshotMountDir']+'/'+myBakBinlogSuffix+'/*binlog*'))
  if r['returnCode'] == 0:
    log.info("Binlogs were successfully archived (%ssec)" % r['execTime'])
  else:
    log.error("Failed to archive Binlogs !")
  openAndWait("chmod 600 %s" % mysqlBinlogTarPath)

# This function will find correct path by substraction 'word/' from mysqlbackupSuffixDir until it finds a valid path
def findMysqlBackupPath(mountpoint,mysqlbackupSuffixDir,ignoreFailure=False):
  log.debug("Trying %s/%s" % (mountpoint,mysqlbackupSuffixDir) )
  if os.path.exists(mountpoint+'/'+mysqlbackupSuffixDir):
    return mountpoint+'/'+mysqlbackupSuffixDir
  while mysqlbackupSuffixDir.find('/') > 0:
    mysqlbackupSuffixDir = '/'.join(mysqlbackupSuffixDir.split('/')[1:]).strip('/')
    log.debug("Trying %s/%s" % (mountpoint,mysqlbackupSuffixDir) )
    if os.path.exists(mountpoint+'/'+mysqlbackupSuffixDir):
      return mountpoint+'/'+mysqlbackupSuffixDir
      break
  else:
    if not ignoreFailure:
      cleanupFromCrash()
      log.critical("Couldn't determine valid mysqld<Data,Binlog,InnodbData>DirFromLvRoot directory, please specify in config file")
      sys.exit(1)
    else:
      return False

# If DataDir BinlogDir and InnodbDataDir path for backup mysql are not set in config file, try to find them
if not 'mysqldDataDirFromLvRoot' in cfg:
  var['myBakDatadir'] = findMysqlBackupPath(cfg['lvmSnapshotMountDir'],var['mysqlvarDatadir'])
else:
  # Check path is valid
  var['myBakDatadir'] = cfg['lvmSnapshotMountDir']+'/'+cfg['mysqldDataDirFromLvRoot'].strip('/')
  if not os.path.exists(var['myBakDatadir']):
    cleanupFromCrash()
    log.critical("mysqldDataDirFromLvRoot specified in config file %s isn't correct, leave it empty or change setting." % var['myBakDatadir'])
    sys.exit(1)

if not 'mysqldBinlogDirFromLvRoot' in cfg:
  if re.match("[ ./]+",var['mysqlvarBinlogdir']) or len(var['mysqlvarBinlogdir']) == 0:
    var['myBakBinlogDir'] = var['myBakDatadir']
  else:
    var['myBakBinlogDir'] = findMysqlBackupPath(cfg['lvmSnapshotMountDir'],var['mysqlvarBinlogdir'])
else:
  # Check path is valid
  var['myBakBinlogDir'] = cfg['lvmSnapshotMountDir']+'/'+cfg['mysqldBinlogDirFromLvRoot'].strip('/')
  if not os.path.exists(var['myBakBinlogDir']):
    cleanupFromCrash()
    log.critical("mysqldBinlogDirFromLvRoot specified in config file %s isn't correct, leave it empty or change setting." % var['myBakBinlogDir'])
    sys.exit(1)

if not 'mysqldInnodbDataDirFromLvRoot' in cfg:
  if re.match("^[ ./]*$",var['mysqlvarInnodbdir']):
    var['myBakInnodbDir'] = var['myBakDatadir']
    log.debug("mysqldInnodbDataDirFromLvRoot not set in cfg file and 'mysqlvarInnodbdir' (%s)  matches [ ./]*" % var['mysqlvarInnodbdir'])
  else:
    var['myBakInnodbDir'] = findMysqlBackupPath(cfg['lvmSnapshotMountDir'],var['mysqlvarInnodbdir'])
    log.debug("mysqldInnodbDataDirFromLvRoot not set in cfg file, using: %s" % var['myBakInnodbDir'])
else:
  # Check path is valid
  var['myBakInnodbDir'] = cfg['lvmSnapshotMountDir']+'/'+cfg['mysqldInnodbDataDirFromLvRoot'].strip('/')
  if not os.path.exists(var['myBakInnodbDir']):
    cleanupFromCrash()
    log.critical("mysqldInnodbDataDirFromLvRoot specified in config file %s isn't correct, leave it empty or change setting." % var['myBakInnodbDir'])
    sys.exit(1)

# option to add: --open_files_limit=32684
mysqldBakCmd = "%s --no-defaults" % cfg['mysqldBin'] \
+   " --user=%s"      % cfg['mysqlUser'] \
+   " --basedir=%s"   % var['myBaseDir'] \
+   " --pid-file=%s"  % cfg['mysqldBackupPidFile'] \
+   " --socket=%s"    % cfg['mysqldBackupSocketFile'] \
+   " --log-error=%s" % cfg['mysqldBackupErrorLogFile'] \
+   " --datadir=%s"   % var['myBakDatadir'] \
+   " --log-bin=%s"   % var['myBakBinlogDir'] \
+   " --innodb_data_home_dir=%s" % var['myBakInnodbDir'] \
+   " --innodb_data_file_path=%s" % var['myBakInnodbDataFilePath'] \
+   " --innodb_log_group_home_dir=%s" % var['myBakBinlogDir'] \
+   " --innodb_buffer_pool_size=%s" % cfg['mysqldBackupInnodbBufferPoolSize'] \
+   " --innodb_additional_mem_pool_size=%s" % var['myBakInnodbAdditionalMemPoolSize'] \
+   " --innodb_log_file_size=%s" % var['myBakInnodbLogFileSize'] \
+   " --innodb_log_buffer_size=%s" % var['myBakInnodbLogBufferSize'] \
+   " --skip-networking" \
+   " --skip-grant-tables " \
+ var['mysqldExtraOpt']

log.debug("Starting backup mysql server for dump: %s" % mysqldBakCmd)
#sys.exit(0)

# Start Mysql Backup Server
log.info("Starting mysql backup server ...")
openAndDetach(mysqldBakCmd)

# Wait until Mysql Backup Server is up
waitUntilMysqlServerIsUp(cfg['mysqlHostname'], cfg['myCnfCred'], cfg['mysqldBackupSocketFile'], cfg['mysqldBackupPidFile'], 600)

# Run Mysqlcheck
if cfg['mysqlCheck'].lower() == 'true':
  log.info("Starting mysqlcheck ...")
  r = openAndWait("%s --socket=%s -m --auto-repair --all-databases" % (cfg['mysqlcheckBin'],cfg['mysqldBackupSocketFile']))
  if r['returnCode'] == 0:
    log.info("Mysqlcheck didn't find errors or could fix them successfully (%ssec)" % r['execTime'])
  else:
    log.info("Mysqlcheck failed !")

# Get Databases List
dbs = ListDB(cfg['mysqlHostname'],cfg['myCnfCred'],cfg['mysqlSocketFile'])
try:    dbs.remove('performance_schema')
except: pass
  
# Dump databases
for db in dbs:
  mysqldumpFilePath = createMysqldumpFilePath(db)
  if db == 'information_schema': extraMysqldumpOpt = '--single-transaction'
  else: extraMysqldumpOpt = ''
  r = openAndWait("%s --socket=%s --opt %s %s %s | gzip > %s ; exit ${PIPESTATUS[0]}" % (cfg['mysqldumpBin'],cfg['mysqldBackupSocketFile'],cfg['mysqldumpOpt'],extraMysqldumpOpt,db,mysqldumpFilePath),1800,ignoreReturncode=True)
  if r['returnCode'] == 0:
    log.info("Mysqldump took %s for dumping %s Successfully" % (r['execTime'],db))
  else:
    log.error("Mysqldump took %s for failing to dump %s" % (r['execTime'],db))
    log.error("Mysqldump stdout: %s" % r['stdout'])
    log.error("Mysqldump stderr: %s" % r['stderr'])
  openAndWait("chmod 600 %s" % mysqldumpFilePath)
  # Reset owner on mysqldump archives
  openAndWait("chown %s %s" % (cfg['mysqldumpOwner'],mysqldumpFilePath))

killBackupMysqld(120)

# Umount LVM Snapshot
umountLVsnapshot()

# Log percentage used by snapshot
lvSnapshotPercentUsed = openAndWait("lvdisplay -C /dev/%s/%s | gawk -F' ' '{ print $6 }' | tail -n1" % (cfg['mysqlVg'], cfg['mysqldBackupLVName']))['stdout']
log.info("%s percents of LVM snapshot %s was used" % (lvSnapshotPercentUsed,cfg['mysqldBackupLVName']))

# Remove LVM Snapshot
removeLVsnapshot(True)

# Check if LV was removed, it could seems to have failed but have success due to bug "file descriptor 3 is still opened"
if os.path.exists('/dev/'+cfg['mysqlVg']+'/'+cfg['mysqldBackupLVName']):
  log.error("LV %s still exists :(" % cfg['mysqldBackupLVName'])
else:
  log.info("LV %s was removed successfully" % cfg['mysqldBackupLVName'])

ts3 = time.time()
fullDuration = ts3 - ts0
log.info("Complete Backup process took %s seconds" % fullDuration)

