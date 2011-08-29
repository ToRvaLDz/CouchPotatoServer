from argparse import ArgumentParser
from couchpotato import web
from couchpotato.api import api
from couchpotato.core.event import fireEventAsync
from libs.daemon import createDaemon
from logging import handlers
from werkzeug.contrib.cache import FileSystemCache
import logging
import os.path
import sys

def getOptions(base_path, args):

    # Options
    parser = ArgumentParser(prog = 'CouchPotato.py')
    parser.add_argument('-s', '--datadir', default = os.path.join(base_path, '_data'),
                        dest = 'data_dir', help = 'Absolute or ~/ path, where settings/logs/database data is saved (default ./_data)')
    parser.add_argument('-t', '--test', '--debug', action = 'store_true',
                        dest = 'debug', help = 'Debug mode')
    parser.add_argument('-q', '--quiet', action = 'store_true',
                        dest = 'quiet', help = "Don't log to console")
    parser.add_argument('-d', '--daemon', action = 'store_true',
                        dest = 'daemonize', help = 'Daemonize the app')
    parser.add_argument('-g', '--nogit', action = 'store_true',
                        dest = 'git', help = 'Running from git')

    options = parser.parse_args(args)

    options.data_dir = os.path.expanduser(options.data_dir)

    return options


def cmd_couchpotato(options, base_path, args):
    '''Commandline entry point.'''

    # Create data dir if needed
    if not os.path.isdir(options.data_dir):
        os.makedirs(options.data_dir)

    # Create logging dir
    log_dir = os.path.join(options.data_dir, 'logs');
    if not os.path.isdir(log_dir):
        os.mkdir(log_dir)


    # Daemonize app
    if options.daemonize:
        createDaemon()


    # Register environment settings
    from couchpotato.environment import Env
    Env.get('settings').setFile(os.path.join(options.data_dir, 'settings.conf'))
    Env.set('uses_git', not options.git)
    Env.set('app_dir', base_path)
    Env.set('data_dir', options.data_dir)
    Env.set('log_path', os.path.join(log_dir, 'CouchPotato.log'))
    Env.set('db_path', 'sqlite:///' + os.path.join(options.data_dir, 'couchpotato.db'))
    Env.set('cache_dir', os.path.join(options.data_dir, 'cache'))
    Env.set('cache', FileSystemCache(os.path.join(Env.get('cache_dir'), 'python')))
    Env.set('quiet', options.quiet)
    Env.set('daemonize', options.daemonize)
    Env.set('args', args)

    # Determine debug
    debug = options.debug or Env.setting('debug', default = False)
    Env.set('debug', debug)


    # Logger
    logger = logging.getLogger()
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%H:%M:%S')
    level = logging.DEBUG if debug else logging.INFO
    logger.setLevel(level)

    # To screen
    if debug and not options.quiet and not options.daemonize:
        hdlr = logging.StreamHandler(sys.stderr)
        hdlr.setFormatter(formatter)
        logger.addHandler(hdlr)

    # To file
    hdlr2 = handlers.RotatingFileHandler(Env.get('log_path'), 'a', 500000, 10)
    hdlr2.setFormatter(formatter)
    logger.addHandler(hdlr2)

    # Disable server access log
    server_log = logging.getLogger('werkzeug')
    server_log.disabled = True

    # Start logging
    from couchpotato.core.logger import CPLog
    log = CPLog(__name__)
    log.debug('Started with options %s' % options)


    # Load configs & plugins (only run once when debugging)
    if os.environ.get('WERKZEUG_RUN_MAIN') or not debug:
        loader = Env.get('loader')
        loader.preload(root = base_path)
        loader.run()


        # Load migrations
        from migrate.versioning.api import version_control, db_version, version, upgrade
        db = Env.get('db_path')
        repo = os.path.join(base_path, 'couchpotato', 'core', 'migration')
        logging.getLogger('migrate').setLevel(logging.WARNING) # Disable logging for migration

        latest_db_version = version(repo)

        try:
            current_db_version = db_version(db, repo)
        except:
            version_control(db, repo, version = latest_db_version)
            current_db_version = db_version(db, repo)

        if current_db_version < latest_db_version and not debug:
            log.info('Doing database upgrade. From %d to %d' % (current_db_version, latest_db_version))
            upgrade(db, repo)

        # Configure Database
        from couchpotato.core.settings.model import setup
        setup()

        fireEventAsync('app.load')

    # Create app
    from couchpotato import app
    api_key = Env.setting('api_key')
    url_base = '/' + Env.setting('url_base').lstrip('/') if Env.setting('url_base') else ''
    reloader = debug and not options.daemonize

    # Basic config
    app.host = Env.setting('host', default = '0.0.0.0')
    app.port = Env.setting('port', default = 5000)
    app.debug = debug
    app.secret_key = api_key

    # Static path
    web.add_url_rule(url_base + '/static/<path:filename>',
                      endpoint = 'static',
                      view_func = app.send_static_file)

    # Register modules
    app.register_blueprint(web, url_prefix = '%s/' % url_base)
    app.register_blueprint(api, url_prefix = '%s/%s/' % (url_base, api_key))

    # Go go go!
    app.run(use_reloader = reloader)
