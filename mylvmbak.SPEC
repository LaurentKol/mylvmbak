%define progName mylvmbak
%define srcVersion 0.5

Summary: Mysql Backup script using lvm snapshot 
Name: %{progName}
Version: %{srcVersion}
Release: 1
License: GPLv2
Group: Utilities/Archiving
URL: https://github.com/LaurentKol/mylvmbak

Packager: Laurent Kolakofsky <laurent.kol@gmail.com>
Vendor: none

Source1: %{progName}.py
Source2: %{progName}.passive-node.py
Source3: %{progName}.cfg
Source4: %{progName}.logrotate

BuildRoot: %{_builddir}/%{name}-%{version}-%{release}-root
BuildArch: noarch

Requires: MySQL-python

%description
This script do a mysqldump of all databases the following way:
1. Lock mysql DB
2. Snapshot its Logical Volume
3. Unlock the DB
4. Start a mysqld backup instance
5. Mysqldump all the databases to a sql.gz file in a backup location

%prep

%build

%install
%{__rm} -rf %{buildroot}
mkdir -p %{buildroot}/%{_sysconfdir}/logrotate.d
mkdir -p %{buildroot}%{_prefix}/sbin
mkdir -p %{buildroot}/etc
mkdir -p %{buildroot}/etc/logrotate.d

# Copying files
install -m 0750 %{SOURCE1} %{buildroot}%{_prefix}/sbin/%{progName}
install -m 0750 %{SOURCE2} %{buildroot}%{_prefix}/sbin/%{progName}-passive-node
install -m 0644 %{SOURCE3} %{buildroot}/etc/%{progName}.cfg
install -m 0644 %{SOURCE4} %{buildroot}/etc/logrotate.d/%{progName}

%clean
%{__rm} -rf %{buildroot}

%files
%defattr (-, root, root, 0755)
#%doc INSTALL README
%attr(0744,root,root) %{_prefix}/sbin/%{progName}
%attr(0744,root,root) %{_prefix}/sbin/%{progName}-passive-node
%attr(0644,root,root) %config(noreplace) %{_sysconfdir}/%{progName}.cfg
%attr(0644,root,root) %config(noreplace) %{_sysconfdir}/logrotate.d/%{progName}

%changelog
* Wed Oct 26 2011 Laurent Kolakofsky <laurent.kol@gmail.com> - 0.5
- First rpm release

