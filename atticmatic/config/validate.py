from collections import namedtuple

from ruamel.yaml import load, RoundTripLoader
from ruamel.yaml.parser_ import ParserError
from ruamel.yaml.scanner import ScannerError

from atticmatic.backends.options import capture_argument_parser


def clean_backend_error_message(error_message):
    '''
    Tidy up a backend error message so that it's suitable as a config file error message.
    '''
    if error_message.startswith('invalid ') and ' value:' in error_message:
        return 'Invalid value'

    return error_message[0].upper() + error_message[1:]


Position = namedtuple('Position', ('line_number', 'column_number'))


def yaml_error_position(error):
    '''
    Given a YAML scanner or parse error, return the line and column position where the problem
    occurred.
    '''

    return Position(error.problem_mark.line - 1, error.problem_mark.column - 1)


def sub_command_position(config, sub_command_name):
    '''
    Given a parsed config dict and the name of a backend sub-command, return its line and column
    position in the configuration file.
    '''
    return Position(*config.lc.key(sub_command_name))


def option_position(config, sub_command_name, argument_name, value_position=False):
    '''
    Given a parsed config dict, the name of a backend sub-command, and the name of an argument
    within that sub-command, return the line and column position of the corresponding option name or
    its value in the configuration file.
    '''
    key_or_value = 'value' if value_position else 'key'

    option_name = command_argument_to_config_option(argument_name)
    position_function = getattr(config[sub_command_name].lc, key_or_value)

    try:
        return Position(*position_function(option_name))
    except:
        return Position(*position_function(argument_name))


def argument_error_message(config_filename, error_position, error_message):
    bad_line = open(config_filename).readlines()[error_position.line_number]

    return '\n'.join((
        'File "{}", line {}, column {}:'.format(
            config_filename,
            error_position.line_number + 1,
            error_position.column_number + 1,
        ),
        bad_line.strip('\n'),
        (' ' * error_position.column_number) + '^',
        clean_backend_error_message(error_message),
    ))


class Configuration_error(Exception):
    def __init__(self, config_filename, error_position, error_message):
        super(Configuration_error, self).__init__(
            argument_error_message(config_filename, error_position, error_message)
        )


def load_config_file(config_filename):
    '''
    Given a configuration filename, load it and return the parsed contents as a dict. If the file
    doesn't parse, print an error message and return None.

    Raise an IOError/PermissionError if something file-related goes wrong.
    '''
    config_file = open(config_filename)
    try:
        return load(config_file, RoundTripLoader)
    except (ScannerError, ParserError) as error:
        raise Configuration_error(
            config_filename,
            yaml_error_position(error),
            '{} {}'.format(error.problem, error.context)
        )
    finally:
        config_file.close()


def command_argument_to_config_option(argument_name):
    '''
    Convert a backend argument name to a corresponding config option name.

    Example: "--posix-me-harder" becomes "posix_me_harder"
    '''
    return argument_name.strip('-').replace('-', '_')


class Option_looks_like_argument_error(Exception):
    def __init__(self, argument_name):
        self.argument_name = argument_name
        self.message = 'Unknown option. Did you mean: "{}"'.format(
            command_argument_to_config_option(argument_name),
        )


def config_option_to_command_argument(option_name, value):
    '''
    Given a config-style option, return a tuple of: the corresponding command-line argument string
    that's more appropriate for passing to a backend command, and its value. Return an empty tuple
    if the value is None or False. Simply omit the value if it is True.

    Example: "posix_me_harder" with a value of 123 becomes "--posix-me-harder 123"

    If the option name given already looks like a command argument, then something is amiss, so
    raise an exception.
    '''
    if command_argument_to_config_option(option_name) != option_name:
        raise Option_looks_like_argument_error(option_name)

    if value in (None, False):
        return ()

    name = '--' + option_name.replace('_', '-')
    if value is True:
        return (name,)

    return (name, str(value))


def sub_command_options_to_command_arguments(options):
    '''
    Given the options dict for a backend sub-command, convert it to command-line arguments and
    return it as a tuple.
    '''
    return tuple(
        item
        for option_name, value in options.items()
        for item in config_option_to_command_argument(option_name, value)
    )


def validate_config_file(backend, config_filename):
    '''
    Given an atticmatic backend and a configuration filename, validate the file and return the
    loaded configuration dict.

    Raise an IOError/PermissionError if something file-related goes wrong, or raise a
    Configuration_error if something is wrong with the contents of the file itself.
    '''
    config = load_config_file(config_filename)
    if not config:
        return

    parser = capture_argument_parser(backend.ARCHIVER_CLASS, backend.ARGUMENT_PARSER_CLASS)

    sub_command_additional_args = {
        'check': ('repository',),
        'create': ('repository::archive', '/path'),
        'prune': ('repository',),
    }

    # Monkeypatch ArgumentParser.error() with our own version that, instead of printing usage and
    # exiting, just raises the current error. That way, we can catch and introspect the error.
    def just_raise(self, message): raise
    parser.__class__.error = just_raise

    for sub_command_name, options in config.items():
        try:
            additional_args = sub_command_additional_args.get(sub_command_name)
            if not additional_args:
                raise Configuration_error(
                    config_filename,
                    sub_command_position(config, sub_command_name),
                    'Unknown section',
                )

            args, argv = parser.parse_known_args(
                (sub_command_name,) +
                sub_command_options_to_command_arguments(options) +
                sub_command_additional_args.get(sub_command_name)
            )
            if argv:
                raise Configuration_error(
                    config_filename,
                    option_position(config, sub_command_name, argv[0]),
                    'Unknown option',
                )
        except Option_looks_like_argument_error as error:
            raise Configuration_error(
                config_filename,
                option_position(config, sub_command_name, error.argument_name),
                error.message,
            )
        except Exception as error:
            if not hasattr(error, 'argument_name'):
                raise

            raise Configuration_error(
                config_filename,
                option_position(config, sub_command_name, error.argument_name, value_position=True),
                error.message,
            )