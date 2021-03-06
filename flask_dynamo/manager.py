"""Main Flask integration."""


from os import environ

from boto.dynamodb2 import connect_to_region
from boto.dynamodb2.table import Table
from flask import (
    _app_ctx_stack as stack,
)

from .errors import ConfigurationError

class Dynamo(object):
    """DynamoDB wrapper for Flask."""

    DEFAULT_REGION = 'us-east-1'

    def __init__(self, app=None):
        """
        Initialize this extension.

        :param obj app: The Flask application (optional).
        """
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """
        Initialize this extension.

        :param obj app: The Flask application.
        """
        self.init_settings(app or app.config)
        self.check_settings(app or app.config)

    def init_settings(self, app):
        """Initialize all of the extension settings."""
        app.config.setdefault('DYNAMO_TABLES', [])
        app.config.setdefault('DYNAMO_ENABLE_LOCAL', environ.get('DYNAMO_ENABLE_LOCAL', False))
        app.config.setdefault('DYNAMO_LOCAL_HOST', environ.get('DYNAMO_LOCAL_HOST'))
        app.config.setdefault('DYNAMO_LOCAL_PORT', environ.get('DYNAMO_LOCAL_PORT'))
        app.config.setdefault('AWS_ACCESS_KEY_ID', environ.get('AWS_ACCESS_KEY_ID'))
        app.config.setdefault('AWS_SECRET_ACCESS_KEY', environ.get('AWS_SECRET_ACCESS_KEY'))
        app.config.setdefault('AWS_REGION', environ.get('AWS_REGION', self.DEFAULT_REGION))

    def check_settings(self, app):
        """
        Check all user-specified settings to ensure they're correct.

        We'll raise an error if something isn't configured properly.

        :raises: ConfigurationError
        """
        if not app.config['DYNAMO_TABLES']:
            raise ConfigurationError('You must specify at least one Dynamo table to use.')

        if app.config['DYNAMO_ENABLE_LOCAL'] and not (app.config['DYNAMO_LOCAL_HOST'] and app.config['DYNAMO_LOCAL_PORT']):
            raise ConfigurationError('If you have enabled Dynamo local, you must specify the host and port.')

    def get_app(self, reference_app=None):
        """Helper method that implements the logic to look up an application.
        """
        if reference_app is not None:
            return reference_app
        if self.app is not None:
            return self.app
        ctx = stack.top
        if ctx is not None:
            return ctx.app
        raise RuntimeError('application not registered on db '
                           'instance and no application bound '
                           'to current context')

    @property
    def connection(self):
        """
        Our DynamoDB connection.

        This will be lazily created if this is the first time this is being
        accessed.  This connection is reused for performance.
        """
        ctx = stack.top
        if ctx is not None:
            if not hasattr(ctx, 'dynamo_connection'):
                app = self.get_app()
                kwargs = {
                    'aws_access_key_id': app.config.get('AWS_ACCESS_KEY_ID'),
                    'aws_secret_access_key': app.config.get('AWS_SECRET_ACCESS_KEY'),
                    'host': app.config['DYNAMO_LOCAL_HOST'] if app.config['DYNAMO_ENABLE_LOCAL'] else None,
                    'port': int(app.config['DYNAMO_LOCAL_PORT']) if app.config['DYNAMO_ENABLE_LOCAL'] else None,
                    'is_secure': not app.config.get('DYNAMO_ENABLE_LOCAL', False),
                }

                if not kwargs['aws_access_key_id'] or not kwargs['aws_secret_access_key']:
                    del kwargs['aws_access_key_id']
                    del kwargs['aws_secret_access_key']

                # If DynamoDB local is disabled, we'll remove these settings.
                if not kwargs['host']:
                    del kwargs['host']
                if not kwargs['port']:
                    del kwargs['port']

                ctx.dynamo_connection = connect_to_region(app.config['AWS_REGION'], **kwargs)

            return ctx.dynamo_connection

    @property
    def tables(self):
        """
        Our DynamoDB tables.

        These will be lazily initializes if this is the first time the tables
        are being accessed.
        """
        ctx = stack.top
        if ctx is not None:
            if not hasattr(ctx, 'dynamo_tables'):
                app = self.get_app()
                ctx.dynamo_tables = {}
                for table in app.config['DYNAMO_TABLES']:
                    table.connection = self.connection
                    ctx.dynamo_tables[table.table_name] = table

                    if not hasattr(ctx, 'dynamo_table_%s' % table.table_name):
                        setattr(ctx, 'dynamo_table_%s' % table.table_name, table)

            return ctx.dynamo_tables

    def __getattr__(self, name):
        """
        Override the get attribute built-in method.

        This will allow us to provide a simple table API.  Let's say a user
        defines two tables: `users` and `groups`.  In this case, our
        customization here will allow the user to access these tables by calling
        `dynamo.users` and `dynamo.groups`, respectively.

        :param str name: The DynamoDB table name.
        :rtype: object
        :returns: A Table object if the table was found.
        :raises: AttributeError on error.
        """

        if hasattr(self, name):
            return getattr(self, name)

        if name in self.tables:
            return self.tables[name]
        raise AttributeError

    def create_all(self):
        """
        Create all user-specified DynamoDB tables.

        We'll error out if the tables can't be created for some reason.
        """
        for table_name, table in self.tables.items():
            Table.create(
                table_name = table.table_name,
                schema = table.schema,
                throughput = table.throughput,
                indexes = table.indexes,
                global_indexes = table.global_indexes,
                connection = self.connection,
            )

    def destroy_all(self):
        """
        Destroy all user-specified DynamoDB tables.

        We'll error out if the tables can't be destroyed for some reason.
        """
        for table_name, table in self.tables.items():
            table.delete()
