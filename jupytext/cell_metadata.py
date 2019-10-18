"""
Convert between text notebook metadata and jupyter cell metadata.

Standard cell metadata are documented here:
See also https://ipython.org/ipython-doc/3/notebook/nbformat.html#cell-metadata
"""

import ast
import re
from json import loads, dumps

try:
    from json import JSONDecodeError
except ImportError:
    JSONDecodeError = ValueError

from .languages import _JUPYTER_LANGUAGES

try:
    unicode  # Python 2
except NameError:
    unicode = str  # Python 3

# Map R Markdown's "echo" and "include" to "hide_input" and "hide_output", that are understood by the `runtools`
# extension for Jupyter notebook, and by nbconvert (use the `hide_input_output.tpl` template).
# See http://jupyter-contrib-nbextensions.readthedocs.io/en/latest/nbextensions/runtools/readme.html
_BOOLEAN_OPTIONS_DICTIONARY = [('hide_input', 'echo', True),
                               ('hide_output', 'include', True)]
_JUPYTEXT_CELL_METADATA = [
    # Pre-jupytext metadata
    'skipline', 'noskipline',
    # Jupytext metadata
    'cell_marker', 'lines_to_next_cell', 'lines_to_end_of_cell_marker']
_IGNORE_CELL_METADATA = ','.join('-{}'.format(name) for name in [
    # Frequent cell metadata that should not enter the text representation
    # (these metadata are preserved in the paired Jupyter notebook).
    'autoscroll', 'collapsed', 'scrolled', 'trusted', 'ExecuteTime'] +
                                 _JUPYTEXT_CELL_METADATA)

_IDENTIFIER_RE = re.compile(r'^[a-zA-Z_\\.]+[a-zA-Z0-9_\\.]*$')


def _r_logical_values(pybool):
    return 'TRUE' if pybool else 'FALSE'


class RLogicalValueError(Exception):
    """Incorrect value for R boolean"""


class RMarkdownOptionParsingError(Exception):
    """Error when parsing Rmd cell options"""


def _py_logical_values(rbool):
    if rbool in ['TRUE', 'T']:
        return True
    if rbool in ['FALSE', 'F']:
        return False
    raise RLogicalValueError


def metadata_to_rmd_options(language, metadata):
    """
    Convert language and metadata information to their rmd representation
    :param language:
    :param metadata:
    :return:
    """
    options = (language or 'R').lower()
    if 'name' in metadata:
        options += ' ' + metadata['name'] + ','
        del metadata['name']
    for jupyter_option, rmd_option, rev in _BOOLEAN_OPTIONS_DICTIONARY:
        if jupyter_option in metadata:
            options += ' {}={},'.format(
                rmd_option, _r_logical_values(metadata[jupyter_option] != rev))
            del metadata[jupyter_option]
    for opt_name in metadata:
        opt_value = metadata[opt_name]
        opt_name = opt_name.strip()
        if opt_name == 'active':
            options += ' {}="{}",'.format(opt_name, str(opt_value))
        elif isinstance(opt_value, bool):
            options += ' {}={},'.format(
                opt_name, 'TRUE' if opt_value else 'FALSE')
        elif isinstance(opt_value, list):
            options += ' {}={},'.format(
                opt_name, 'c({})'.format(
                    ', '.join(['"{}"'.format(str(v)) for v in opt_value])))
        else:
            options += ' {}={},'.format(opt_name, str(opt_value))
    if not language:
        options = options[2:]
    return options.strip(',').strip()


def update_metadata_from_rmd_options(name, value, metadata):
    """
    Update metadata using the _BOOLEAN_OPTIONS_DICTIONARY mapping
    :param name: option name
    :param value: option value
    :param metadata:
    :return:
    """
    for jupyter_option, rmd_option, rev in _BOOLEAN_OPTIONS_DICTIONARY:
        if name == rmd_option:
            try:
                metadata[jupyter_option] = _py_logical_values(value) != rev
                return True
            except RLogicalValueError:
                pass
    return False


class ParsingContext:
    """
    Class for determining where to split rmd options
    """
    parenthesis_count = 0
    curly_bracket_count = 0
    square_bracket_count = 0
    in_single_quote = False
    in_double_quote = False

    def __init__(self, line):
        self.line = line

    def in_global_expression(self):
        """Currently inside an expression"""
        return (self.parenthesis_count == 0 and self.curly_bracket_count == 0
                and self.square_bracket_count == 0
                and not self.in_single_quote and not self.in_double_quote)

    def count_special_chars(self, char, prev_char):
        """Update parenthesis counters"""
        if char == '(':
            self.parenthesis_count += 1
        elif char == ')':
            self.parenthesis_count -= 1
            if self.parenthesis_count < 0:
                raise RMarkdownOptionParsingError(
                    'Option line "{}" has too many '
                    'closing parentheses'.format(self.line))
        elif char == '{':
            self.curly_bracket_count += 1
        elif char == '}':
            self.curly_bracket_count -= 1
            if self.curly_bracket_count < 0:
                raise RMarkdownOptionParsingError(
                    'Option line "{}" has too many '
                    'closing curly brackets'.format(self.line))
        elif char == '[':
            self.square_bracket_count += 1
        elif char == ']':
            self.square_bracket_count -= 1
            if self.square_bracket_count < 0:
                raise RMarkdownOptionParsingError(
                    'Option line "{}" has too many '
                    'closing square brackets'.format(self.line))
        elif char == "'" and prev_char != '\\':
            self.in_single_quote = not self.in_single_quote
        elif char == '"' and prev_char != '\\':
            self.in_double_quote = not self.in_double_quote


def parse_rmd_options(line):
    """
    Given a R markdown option line, returns a list of pairs name,value
    :param line:
    :return:
    """
    parsing_context = ParsingContext(line)

    result = []
    prev_char = ''

    name = ''
    value = ''

    for char in ',' + line + ',':
        if parsing_context.in_global_expression():
            if char == ',':
                if name != '' or value != '':
                    if result and name == '':
                        raise RMarkdownOptionParsingError(
                            'Option line "{}" has no name for '
                            'option value {}'.format(line, value))
                    result.append((name.strip(), value.strip()))
                    name = ''
                    value = ''
            elif char == '=':
                if name == '':
                    name = value
                    value = ''
                else:
                    value += char
            else:
                parsing_context.count_special_chars(char, prev_char)
                value += char
        else:
            parsing_context.count_special_chars(char, prev_char)
            value += char
        prev_char = char

    if not parsing_context.in_global_expression():
        raise RMarkdownOptionParsingError(
            'Option line "{}" is not properly terminated'.format(line))

    return result


def rmd_options_to_metadata(options):
    """
    Parse rmd options and return a metadata dictionary
    :param options:
    :return:
    """
    options = re.split(r'\s|,', options, 1)
    if len(options) == 1:
        language = options[0]
        chunk_options = []
    else:
        language, others = options
        language = language.rstrip(' ,')
        others = others.lstrip(' ,')
        chunk_options = parse_rmd_options(others)

    language = 'R' if language == 'r' else language
    metadata = {}
    for i, opt in enumerate(chunk_options):
        name, value = opt
        if i == 0 and name == '':
            metadata['name'] = value
            continue
        if update_metadata_from_rmd_options(name, value, metadata):
            continue
        try:
            metadata[name] = _py_logical_values(value)
            continue
        except RLogicalValueError:
            metadata[name] = value

    for name in metadata:
        try_eval_metadata(metadata, name)

    if 'eval' in metadata and not is_active('.Rmd', metadata):
        del metadata['eval']

    return metadata.get('language') or language, metadata


def metadata_to_md_options(metadata):
    """Encode {'class':None, 'key':'value'} into 'class key="value"' """

    return ' '.join(["{}={}".format(key, dumps(metadata[key]))
                     if metadata[key] is not None else key for key in metadata])


def try_eval_metadata(metadata, name):
    """Evaluate given metadata to a python object, if possible"""
    value = metadata[name]
    if not isinstance(value, (str, unicode)):
        return
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        if name in ['active', 'magic_args', 'language']:
            metadata[name] = value[1:-1]
        return
    if value.startswith('c(') and value.endswith(')'):
        value = '[' + value[2:-1] + ']'
    elif value.startswith('list(') and value.endswith(')'):
        value = '[' + value[5:-1] + ']'
    try:
        metadata[name] = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return


def is_active(ext, metadata, default=True):
    """Is the cell active for the given file extension?"""
    if metadata.get('run_control', {}).get('frozen') is True:
        return ext == '.ipynb'
    for tag in metadata.get('tags', []):
        if tag.startswith('active-'):
            return ext.replace('.', '') in tag.split('-')
    if 'active' not in metadata:
        return default
    return ext.replace('.', '') in re.split('\\.|,', metadata['active'])


def metadata_to_double_percent_options(metadata):
    """Metadata to double percent lines"""
    text = []
    if 'title' in metadata:
        text.append(metadata.pop('title'))
    if 'cell_depth' in metadata:
        text.insert(0, '%' * metadata.pop('cell_depth'))
    if 'cell_type' in metadata:
        text.append('[{}]'.format(metadata.pop('region_name', metadata.pop('cell_type'))))
    return metadata_to_text(' '.join(text), metadata, plain_json=True)


def incorrectly_encoded_metadata(text):
    """Encode a text that Jupytext cannot parse as a cell metadata"""
    return {'incorrectly_encoded_metadata': text}


def isidentifier(text):
    """Can this text be a proper key?"""
    return _IDENTIFIER_RE.match(text)


def is_jupyter_language(language):
    """Is this a jupyter language?"""
    for lang in _JUPYTER_LANGUAGES:
        if language.lower() == lang.lower():
            return True
    return False


def parse_key_equal_value(text):
    """Parse a string of the form 'key1=value1 key2=value2'"""
    # Empty metadata?
    text = text.strip()
    if not text:
        return {}

    last_space_pos = text.rfind(' ')

    # Just an identifier?
    if isidentifier(text[last_space_pos + 1:]):
        key = text[last_space_pos + 1:]
        value = None
        result = {key: value}
        if last_space_pos > 0:
            result.update(parse_key_equal_value(text[:last_space_pos]))
        return result

    # Iterate on the '=' signs, starting from the right
    equal_sign_pos = None
    while True:
        equal_sign_pos = text.rfind('=', None, equal_sign_pos)
        if equal_sign_pos < 0:
            return incorrectly_encoded_metadata(text)

        # Do we have an identifier on the left of the equal sign?
        prev_whitespace = text[:equal_sign_pos].rstrip().rfind(' ')
        key = text[prev_whitespace + 1:equal_sign_pos].strip()
        if not isidentifier(key.replace('.', '')):
            continue

        try:
            value = relax_json_loads(text[equal_sign_pos + 1:])
        except (ValueError, SyntaxError):
            # try with a longer expression
            continue

        # Combine with remaining metadata
        metadata = parse_key_equal_value(text[:prev_whitespace]) if prev_whitespace > 0 else {}

        # Append our value
        metadata[key] = value

        # And return
        return metadata


def relax_json_loads(text, catch=False):
    """Parse a JSON string or similar"""
    text = text.strip()
    try:
        return loads(text)
    except JSONDecodeError:
        pass

    if not catch:
        return ast.literal_eval(text)

    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        pass

    return incorrectly_encoded_metadata(text)


def text_to_metadata(text, allow_title=False):
    """Parse the language/cell title and associated metadata"""
    # Parse the language or cell title = everything before the last blank space before { or =
    text = text.strip()
    first_curly_bracket = text.find('{')
    first_equal_sign = text.find('=')

    if first_curly_bracket < 0 and first_equal_sign < 0 and allow_title:
        # no metadata
        return text, {}

    if first_curly_bracket < 0 or (0 <= first_equal_sign < first_curly_bracket):
        # this is a key=value metadata line
        if allow_title:
            prev_whitespace = text[:first_equal_sign].rstrip().rfind(' ')
        else:
            prev_whitespace = text.find(' ')

        # single expression
        if prev_whitespace < 0:
            # is is a title or a jupyter language?
            if (allow_title and first_equal_sign < 0) or is_jupyter_language(text):
                return text, {}

            # else, parse this expression as a key
            prev_whitespace = 0

        return text[:prev_whitespace].rstrip(), parse_key_equal_value(text[prev_whitespace:])

    # json metadata line
    return text[:first_curly_bracket].strip(), relax_json_loads(text[first_curly_bracket:], catch=True)


def metadata_to_text(language_or_title, metadata=None, plain_json=False):
    """Write the cell metadata in the format key=value"""
    # Was metadata the first argument?
    if metadata is None:
        metadata, language_or_title = language_or_title, metadata

    metadata = {key: metadata[key] for key in metadata if key not in _JUPYTEXT_CELL_METADATA}
    text = [language_or_title] if language_or_title else []
    if language_or_title is None:
        if 'title' in metadata and '{' not in metadata['title'] and '=' not in metadata['title']:
            text.append(metadata.pop('title'))

    if plain_json:
        if metadata:
            text.append(dumps(metadata))
    else:
        for key in metadata:
            if key == 'incorrectly_encoded_metadata':
                text.append(metadata[key])
            elif metadata[key] is None:
                text.append(key)
            else:
                text.append('{}={}'.format(key, dumps(metadata[key])))
    return ' '.join(text)
