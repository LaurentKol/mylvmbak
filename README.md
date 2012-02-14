# mylvmbak
Mysql backup python script using LVM snapshot and mysqldump

Basically, it does this:
 1. Lock mysql DB
 2. Snapshot its Logical Volume
 3. Unlock the DB
 4. Start a mysqld backup instance
 5. Mysqldump all the databases to a sql.gz file in a backup location

#### Install
 * `yum -y install MySQL-python`
 * Create mysql user: `GRANT SELECT, RELOAD, LOCK TABLES, SHOW VIEW ON *.* TO 'backup'@'127.0.0.1' IDENTIFIED BY PASSWORD 'xxx'`
 * Create `.my.cnf` file with credentials

