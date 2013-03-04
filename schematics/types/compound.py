try:
    from itertools import filterfalse  # python3 wutwut
except:
    from itertools import ifilterfalse
from operator import itemgetter

from .base import BaseType, DictType

from .. import Model
from ..datastructures import MultiValueDict
from ..serialize import to_python, to_json, for_jsonschema
from ..validation import ValidationError, validate_instance, validate_values


RECURSIVE_REFERENCE_CONSTANT = 'self'


class ListType(BaseType):
    """A list type that wraps a standard type, allowing multiple instances
    of the type to be used as a list in the model.
    """

    def __init__(self, fields, **kwargs):
        super(ListType, self).__init__(**kwargs)

        ### Short hand
        is_basetype = lambda field: isinstance(field, BaseType)
        is_model = lambda field: isinstance(field, ModelType)
        is_dicttype = lambda field: isinstance(field, DictType)

        ### fields is a schematic Type
        if is_basetype(fields):
            if is_model(fields):
                kwargs.setdefault('primary_embedded', fields)
            fields = [fields]

        assert isinstance(fields, list)
        for field in fields:
            if not is_basetype(field):
                raise ValueError, (u'Argument to ListType constructor must be a '
                                    'valid field or list of valid fields')
        assert not list(ifilterfalse(is_basetype, fields))

        models = filter(is_model, fields)
        dicts = filter(is_dicttype, fields)
        if dicts:
            kwargs.setdefault('primary_embedded', None)
        if models:
            kwargs.setdefault('primary_embedded', models[0])

        self.fields = fields
        kwargs.setdefault('default', list)

        self.primary_embedded = kwargs.pop('primary_embedded', None)

    def __set__(self, instance, value_list):
        """Descriptor for assigning a value to a type in a model.
        """
        new_value = value_list

        is_model = lambda tipe: isinstance(tipe, ModelType)
        model_fields = filter(is_model, self.fields)

        if self.primary_embedded:
            model_fields.remove(self.primary_embedded)
            model_fields.insert(0, self.primary_embedded)

        if value_list is None:
            value_list = []  # have to use a list

        errors_found = False
        if model_fields:
            new_data = list()
            for datum in value_list:
                datum_instance = None
                is_dict = False

                ### Extract field names from datum
                datum_fields = None
                if isinstance(datum, dict):
                    datum_fields = datum.keys()
                else:
                    datum_fields = datum._fields.keys()

                ### Determine matching model
                for model_field in model_fields:
                    test_keys = model_field.model_type_obj._fields.keys()
                    datum_keys = datum_fields
                    if set(test_keys) == set(datum_keys):
                        if not isinstance(datum, dict):
                            datum = datum._data
                        datum_instance = model_field.model_type_obj(**datum)

                ### Validate model
                values, errors = validate_instance(datum_instance)
                if not errors:
                    new_data.append(datum_instance)
                else:
                    errors_found = True

            new_value = new_data

        if not errors_found:
            instance._data[self.field_name] = new_value

    def _jsonschema_type(self):
        return 'array'

    @classmethod
    def _from_jsonschema_types(self):
        return ['array']


    @classmethod
    def _from_jsonschema_formats(self):
        return [None]

    def _jsonschema_items(self):
        return [for_jsonschema(field) for field in self.fields]

    def for_output_format(self, output_format_method_name, value):
        for item in value:
            for field in self.fields:
                try:
                    value = getattr(field, output_format_method_name)(item)
                    yield value
                except ValueError:
                    continue

    def for_python(self, value):
        return list(self.for_output_format('for_python', value))

    def for_json(self, value):
        """for_json must be careful to expand modeltypes into Python,
        not JSON.
        """
        return list(self.for_output_format('for_json', value))

    def process(self, value):
        """Make sure that a list of valid fields is being used.
        """
        if not isinstance(value, (list, tuple)):
            raise ValueError, 'Only lists and tuples may be used in a list field'

        if not self.fields:  # empty list
            return value

        errors = []
        values = []
        for i, item in enumerate(value, 1):
            for field in self.fields:
                if field.validate(item):
                    values.append(field.clean)
                else:
                    # Label errors by their index
                    errors.append(('index-' + str(i), field.errors))

        if len(errors) > 0:
            raise ValidationError, errors

        return values

    def _set_owner_model(self, owner_model):
        for field in self.fields:
            field.owner_model = owner_model
        self._owner_model = owner_model

    def _get_owner_model(self, owner_model):
        self._owner_model = owner_model

    owner_model = property(_get_owner_model, _set_owner_model)


class SortedListType(ListType):
    """A ListType that sorts the contents of its list before writing to
    the database in order to ensure that a sorted list is always
    retrieved.
    """

    _ordering = None

    def __init__(self, field, **kwargs):
        if 'ordering' in kwargs.keys():
            self._ordering = kwargs.pop('ordering')
        super(SortedListType, self).__init__(field, **kwargs)

    def for_thing(self, value, meth):
        unsorted = getattr(super(SortedListType, self), meth)(value)
        if self._ordering is not None:
            return sorted(unsorted, key=itemgetter(self._ordering))
        return sorted(unsorted)

    def for_python(self, value):
        return self.for_thing(value, 'for_python')

    def for_json(self, value):
        return self.for_thing(value, 'for_json')

    @classmethod
    def _from_jsonschema_types(self):
        return []

    @classmethod
    def _from_jsonschema_formats(self):
        return []


###
### Sub schematics
###

class ModelType(BaseType):
    """A model field. Only valid values are subclasses of `schematics.Model`.
    """
    def __init__(self, model_type, **kwargs):
        is_embeddable = lambda dt: issubclass(dt, Model)
        if not isinstance(model_type, basestring):
            if not model_type or not is_embeddable(model_type):
                raise ValueError, 'Invalid model class provided to an ModelType'
        self.model_type_obj = model_type
        super(ModelType, self).__init__(**kwargs)

    def __set__(self, instance, value):
        if value is None:
            return
        if not isinstance(value, self.model_type):
            value = self.model_type(**value)
        instance._data[self.field_name] = value

    @property
    def model_type(self):
        if isinstance(self.model_type_obj, basestring):
            if self.model_type_obj == RECURSIVE_REFERENCE_CONSTANT:
                self.model_type_obj = self.owner_model
            else:
                self.model_type_obj = get_model(self.model_type_obj)
        return self.model_type_obj

    def _jsonschema_type(self):
        return 'object'

    @classmethod
    def _from_jsonschema_types(self):
        return ['object']

    @classmethod
    def _from_jsonschema_formats(self):
        return [None]

    def for_jsonschema(self):
        return for_jsonschema(self.model_type)

    def for_python(self, value):
        return to_python(value)

    def for_json(self, value):
        return to_json(value, encode=False)

    def process(self, value):
        """Make sure that the model instance is an instance of the
        Model subclass provided when the model was defined.
        """
        # Using isinstance also works for subclasses of self.model

        if isinstance(value, dict):
            value = self.model_type(**value)

        if not isinstance(value, self.model_type):
            raise ValueError, 'Invalid modeltype instance provided to an ModelType'

        values, errors = validate_instance(value)
        if errors:
            raise ValidationError, errors
        return values

    def lookup_member(self, member_name):
        return self.model_type._fields.get(member_name)


class MultiValueDictType(DictType):
    def __init__(self, basecls=None, *args, **kwargs):
        self.basecls = basecls or BaseType

        assert not issubclass(self.basecls, BaseType), 'basecls is not subclass of BaseType'

        kwargs.setdefault('default', lambda: MultiValueDict())
        super(MultiValueDictType, self).__init__(*args, **kwargs)

    def __set__(self, instance, value):
        if value is not None and not isinstance(value, MultiValueDict):
            value = MultiValueDict(value)

        super(MultiValueDictType, self).__set__(instance, value)

    def process(self, value):
        """Make sure that a list of valid fields is being used.
        """
        if not isinstance(value, (dict, MultiValueDict)):
            raise ValueError, 'Only dictionaries or MultiValueDict may be used in a '

        ### TODO get rid of this
        if any(('.' in k or '$' in k) for k in value):
            error_msg = 'Invalid dictionary key name - keys may not contain '
            error_msg = error_msg + '"." or "$" characters'
            return FieldResult(ERROR_FIELD_TYPE_CHECK, error_msg,
                               self.field_name, value)

        return value

    def for_json(self, value):
        output = {}
        for key, values in value.iterlists():
            output[key] = values

        return output
