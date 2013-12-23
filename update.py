#!/usr/bin/python
'''
Nagios labs bot's mysql creator

Author: Damian Zaremba <damian@damianzaremba.co.uk>

This program is free software. It comes without any warranty, to
the extent permitted by applicable law. You can redistribute it
and/or modify it under the terms of the Do What The Fuck You Want
To Public License, Version 2, as published by Sam Hocevar. See
http://sam.zoy.org/wtfpl/COPYING for more details.
'''
# Import modules we need
import re
import sys
import os
import ldap
import logging
import MySQLdb
from pwd import getpwnam
from optparse import OptionParser
import random
import string

# Our base dir
base_dir = "/home"

# How much to spam
logging_level = logging.INFO

# LDAP details
ldap_config_file = "/etc/ldap.conf"
ldap_base_dn = "dc=wikimedia,dc=org"
ldap_filter = '(&(objectClass=groupofnames)(cn=bots))'
ldap_attrs = ['member', 'cn']

# MySQL details
mysql_host = "localhost"

# Setup logging, everyone likes logging
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(formatter)
logger = logging.getLogger(__name__)
logger.setLevel(logging_level)
logger.addHandler(stdout_handler)


def get_ldap_config():
    '''
    Simple function to load the ldap config into a dict
    '''
    ldap_config = {}
    with open(ldap_config_file, 'r') as fh:
        for line in fh.readlines():
            line_parts = line.split(' ', 1)

            if len(line_parts) == 2:
                ldap_config[line_parts[0].strip()] = line_parts[1].strip()

    return ldap_config


def get_mysql_config(mysql_config_file="/root/.my.cnf"):
    '''
    Simple function to load the mysql config into a dict
    '''
    mysql_config = {}
    with open(mysql_config_file, 'r') as fh:
        for line in fh.readlines():
            line_parts = line.split('=', 1)

            if len(line_parts) == 2:
                key = line_parts[0].strip()
                if key == 'pass': key = 'password'
                if key == 'user': key = 'username'
                mysql_config[key] = line_parts[1].strip()

    return mysql_config


def mysql_connect():
    '''
    Simple function to connect to mysql
    '''
    username = password = None
    mysql_config = get_mysql_config()

    if 'user' in mysql_config.keys():
        username = mysql_config['user']

    if 'password' in mysql_config.keys():
        password = mysql_config['password']

    if not username or not password:
        print "here"
        return

    db = MySQLdb.connect(host=mysql_host, user=username, passwd=password,
                         db='mysql')
    return db


def mysql_disconnect(db):
    '''
    Simple function to disconnect from mysql
    '''
    db.close()


def ldap_connect():
    '''
    Simple function to connect to ldap
    '''
    ldap_config = get_ldap_config()
    if 'uri' not in ldap_config:
        logger.error('Could get URI from ldap config')
        return False

    if 'binddn' not in ldap_config or 'bindpw' not in ldap_config:
        logger.error('Could get bind details from ldap config')
        return False

    ldap_connection = ldap.initialize(ldap_config['uri'])
    ldap_connection.start_tls_s()

    try:
        ldap_connection.simple_bind_s(ldap_config['binddn'],
                                      ldap_config['bindpw'])
    except ldap.LDAPError:
        logger.error('Could not bind to LDAP')
    else:
        logger.debug('Connected to ldap')
        return ldap_connection


def ldap_disconnect(ldap_connection):
    '''
    Simple function to disconnect from ldap
    '''
    try:
        ldap_connection.unbind_s()
    except ldap.LDAPError:
        logger.error('Could not cleanly disconnect from LDAP')
    else:
        logger.debug('Disconnected from ldap')

if __name__ == "__main__":
    parser = OptionParser()
    parser.add_option('-d', '--debug', action='store_true', dest='debug')

    (options, args) = parser.parse_args()
    if options.debug:
        logger.setLevel(logging.DEBUG)

    # Connect
    ldap_connection = ldap_connect()
    if not ldap_connection:
        logger.error('Could not connect to ldap')
        sys.exit(1)

    mysql_connection = mysql_connect()
    if not mysql_connection:
        logger.error('Could not connect to mysql')
        ldap_disconnect(ldap_connection)
        sys.exit(1)

    # Get the users
    logger.debug('Searching ldap for hosts')
    results = ldap_connection.search_s(ldap_base_dn, ldap.SCOPE_SUBTREE,
                                       ldap_filter, ldap_attrs)

    if not results:
        logger.error('Could not get the list of users from ldap')
        sys.exit(1)

    for (dn, project) in results:
        logger.debug('Processing info for %s' % dn)

    for member in project['member']:
        # We could do another ldap search here but that seems wasteful
        matches = re.match(r'uid=(.+),ou=people,.+', member)
        if not matches or not matches.group(1):
            logger.error('Could not understand %s' % member)
            continue
        username = matches.group(1)

        path = os.path.join(base_dir, username)
        if not os.path.exists(path):
            logger.info('%s does not exist, creating' % path)
            os.makedirs(path)

            if not os.path.exists(path):
                logger.error('Failed to create %s' % path)
                continue
            logger.info('Created %s successfully' % path)

            uid = getpwnam(username).pw_uid
            gid = getpwnam('www-data').pw_uid

            if not uid or not gid:
                logger.error('Could not get uid or gid for %s' % uid)
                continue

            logger.debug('Checking if user exists')

            logger.info('Chowning %s to %d.%d' % (path, uid, gid))
            os.chown(path, uid, gid)

        config_path = os.path.join(path, '.my.cnf')
        password = None
        if os.path.exists(config_path):
            mysql_config = get_mysql_config(config_path)

            if 'password' in mysql_config.keys():
                password = mysql_config['password']

        if not password:
            password = ''.join(random.choice(string.ascii_uppercase +
                               string.digits) for x in range(100))

        cur = mysql_connection.cursor()
        cur.execute("select * from user where User = %s", username)
        if len(cur.fetchall()) > 0:
            logger.info('Skipping %s as mysql users exists' % username)
            continue
        
        logger.info('Creating user %s' % username)
        print "create user '%s' identified by '%s'" % (username, password)
        try:
            cur.execute("create user %s identified by %s",
                        (username, password))
        except:
            logger.exception('Failed to create user %s' % username)
            continue

        logger.debug('granting access to user')
        try:
            cur.execute("grant all privileges on `%s`_*.* to '%s'@'%' with grant option",
                        (username, username))
        except:
            logger.exception('Failed to grant rights to %s' % username)
            continue

        logger.debug('flushing db privileges')
        try:
            cur.execute("flush privileges")
        except:
            pass
        cur.close()

        with open(config_path, 'w') as fh:
            fh.write('[mysql]')
            fh.write('user=%s' % username)
            fh.write('password=%s' % password)
            fh.close()

    ldap_disconnect(ldap_connection)
    mysql_disconnect(mysql_connection)
