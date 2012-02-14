# mylvmbak
Mysql backup python script using LVM snapshot and mysqldump

Basically, it does this:
 1. Lock mysql DB
 2. Snapshot its Logical Volume
 3. Unlock the DB
 4. Start a mysqld backup instance
 5. Mysqldump all the databases to a sql.gz file in a backup location

#### Install
 1. `sudo yum -y install MySQL-python`
 2. Into a user of your choice directory: `git clone https://github.com/LaurentKol/mylvmbak.git`
 3. `chmod u+x mylvmbak.py`
 4. Create mysql user: `GRANT SELECT, RELOAD, LOCK TABLES, SHOW VIEW ON *.* TO 'backup'@'127.0.0.1' IDENTIFIED BY PASSWORD 'xxx'`
 5. Create `.my.cnf` file with credentials
 6. Set up `mylvmbak.cfg` according to your environment
 7. Script ready to run

