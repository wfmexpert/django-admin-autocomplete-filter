"""Defines autocomplete filters and helper functions for the admin."""

import warnings
from django.contrib.admin import SimpleListFilter
from django.contrib.admin.utils import prepare_lookup_value
from django.contrib.admin.widgets import (
    AutocompleteSelect as BaseAutocompleteSelect,
    AutocompleteSelectMultiple as BaseAutocompleteSelectMultiple,
)
from django.core.exceptions import ImproperlyConfigured
from django.db.models.fields.related import ForeignObjectRel
from django.db.models.constants import LOOKUP_SEP  # this is '__'
from django.db.models.fields.related_descriptors import (
    ReverseManyToOneDescriptor, ManyToManyDescriptor,
)
from django.forms import ModelChoiceField, ModelMultipleChoiceField
from django.forms.widgets import Media, MEDIA_TYPES, media_property
from django.shortcuts import reverse


class AutocompleteSelect(BaseAutocompleteSelect):
    """A customize AutocompleteSelect that allows a custom URL."""

    def __init__(self, rel, admin_site, attrs=None, choices=(), using=None, custom_url=None):
        """Initialize class variables for an AutocompleteSelect object."""
        self.custom_url = custom_url
        super().__init__(rel, admin_site, attrs, choices, using)
    
    def get_url(self):
        """Specifies the URL to be used to fetch the autocomplete list."""
        return self.custom_url if self.custom_url else super().get_url()


class AutocompleteSelectMultiple(BaseAutocompleteSelectMultiple):
    """A customize AutocompleteSelectMultiple that allows a custom URL."""

    def __init__(self, rel, admin_site, attrs=None, choices=(), using=None, custom_url=None):
        """Initialize class variables for an AutocompleteSelectMultiple object."""
        self.custom_url = custom_url
        super().__init__(rel, admin_site, attrs, choices, using)

    def get_url(self):
        """Specifies the URL to be used to fetch the autocomplete list."""
        return self.custom_url if self.custom_url else super().get_url()


class AutocompleteFilterMeta(type(SimpleListFilter)):
    """
    A metaclass for setting class-level variables expected by SimpleListFilter.
    When Python 3.5 support is no longer required, this could possibly be
    replaced by a parent class using __init_subclass__, rather than a metaclass.
    """
    def __init__(self, *args, **kwargs):
        """
        Initialize class variables for class `self`, for
        subclasses of AutocompleteFilter.
        """
        super().__init__(*args, **kwargs)
        if self.field_name is not None:
            self.parameter_name = self.get_parameter_name()
            # self.template =  # Default set in class body
            self.title = self.get_title()


class AutocompleteFilter(SimpleListFilter, metaclass=AutocompleteFilterMeta):
    """
    An admin changelist filter that uses a Select2 autocomplete widget.
    Class variables:
        * field_name (str): The name of the target field to filter on, relative
            to the admin model, using '__' as needed.
        * field_pk (str): The name of the primary key to be used for the target
            in the query parameter.
        * form_field (obj): The form field class for the filter, typically a
            child of ModelChoiceField.
        * form_widget (obj): The for widget class for the filter, typically a
            child of the local version of AutocompleteSelect*.
        * is_placeholder_title (bool): A flag for overriding an HTML parameter
            with the filter title.
        * label_by (str, func): How to generate the static label for the widget:
            a callable, the name of a model callable, or the name of a model field.
            (Defaults to str.)
        * multi_select (bool): Whether the filter should allow multiple selections,
            which if true may require manual use of queryset.distinct() to remove
            duplicates.
        * parameter_name (str): The name of the GET parameter to be used.
        * template (str): The template to be used to render the filter widget.
        * title (str): The title at the top of the filter widget.
        * use_pk_exact (bool): Whether to use '__pk__exact' or '__pk__in' in the
            query parameter when possible.
        * view_name (str): The name of the custom AutocompleteJsonView URL to use,
            if any.
        * widget_attrs (dict): Any custom attrs to pass to the widget HTML.
    """

    # ########## Basic configuration ########## #

    field_name = None
    field_pk = 'pk'
    form_field = None
    form_widget = None
    is_placeholder_title = False
    label_by = None
    multi_select = False
    # parameter_name =  # Default set in metaclass; can override by setting in subclass body
    template = 'django-admin-autocomplete-filter/autocomplete-filter.html'  # overrides SimpleListFilter
    # title =  # Default set in metaclass; can override by setting in subclass body
    use_pk_exact = True
    view_name = None
    widget_attrs = {}

    class Media:
        """
        A class for defining static files to be loaded on pages using
        this filter.
        """
        js = (
            'admin/js/jquery.init.js',
            'django-admin-autocomplete-filter/js/autocomplete_filter_qs.js',
        )
        css = {
            'screen': (
                'django-admin-autocomplete-filter/css/autocomplete-fix.css',
            ),
        }

    def __init__(self, request, params, model, model_admin):
        """Initialize class variables for an AutocompleteFilter object."""

        # Check configuration
        self.check_field_name()
        if hasattr(self, 'rel_model'):
            warnings.warn('The rel_model attribute is no longer used.', DeprecationWarning)

        # Init via parent class (after checking field_name)
        super().__init__(request, params, model, model_admin)

        # Instance vars not used, to make argument passing explicit
        rel_model = self.get_rel_model(model)
        ultimate_field_name = self.get_ultimate_field_name()
        remote_field = rel_model._meta.get_field(ultimate_field_name).remote_field
        widget = self.get_widget(request, model_admin, remote_field)
        field = self.get_field(request, model_admin, rel_model, widget)
        self._add_media(model_admin, widget)
        attrs = self.get_attrs(request, model_admin)
        self.rendered_widget = self.render_widget(field, attrs)

    # ########## Class "properties" ########## #
    # We could use @django.utils.functional.classproperty form Django 3.1, or similar

    @classmethod
    def get_parameter_name(cls):
        """
        Get the parameter_name based on class variables, used for HTTP GET parameter.
        Note that you would need to override BaseModelAdmin.lookup_allowed()
        to get custom remote field GET parameter strings.
        """
        if cls.parameter_name is None:
            if LOOKUP_SEP not in cls.field_name:
                return cls.get_query_parameter()
            else:
                return cls.field_name
        else:
            return cls.parameter_name

    @classmethod
    def get_query_parameter(cls):
        """Get the query parameter based on class variables."""
        query_parameter = cls.field_name
        if cls.multi_select:
            if cls.use_pk_exact:  # note that "exact" is a misnomer here
                query_parameter += '__{}__in'.format(cls.field_pk)
            else:
                query_parameter += '__in'.format(cls.field_pk)
        else:
            if cls.use_pk_exact:
                query_parameter += '__{}__exact'.format(cls.field_pk)
            else:
                pass
        return query_parameter

    @classmethod
    def get_title(cls):
        """Get the title based on class variables."""
        if cls.title is None:
            return str(cls.field_name).replace('__', ' - ').replace('_', ' ').title()
        else:
            return cls.title

    @classmethod
    def get_ultimate_field_name(cls):
        """Get the name of the ultimate field based on class variables."""
        return str(cls.field_name).split(LOOKUP_SEP)[-1]

    # ########## Other methods ########## #

    @classmethod
    def check_field_name(cls):
        """
        Check that field_name has been defined.
        Don't call this at AutocompleteFilter creation time - would need to be on subclasses.
        """
        if not hasattr(cls, 'field_name') or cls.field_name is None or cls.field_name == '':
            raise ImproperlyConfigured(
                "The list filter '%s' does not specify a 'field_name'."
                % cls.__name__
            )

    @classmethod
    def get_rel_model(cls, model):
        """
        A way to calculate the model for a field_name that includes LOOKUP_SEP.
        """
        field_names = str(cls.field_name).split(LOOKUP_SEP)
        if len(field_names) == 1:
            return model
        else:
            rel_model = model
            for name in field_names[:-1]:
                rel_model = rel_model._meta.get_field(name).related_model
            return rel_model

    @classmethod
    def generate_choice_field(cls, label_item, form_field, request, model_admin):
        """
        Create a ModelChoiceField variant with a modified label_from_instance.
        May be a ModelMultipleChoiceField if multi-select is enabled, or other custom class.
        Note that label_item can be a callable, or a model field, or a model callable.
        """

        class LabelledModelChoiceField(form_field):
            def label_from_instance(self, obj):
                if callable(label_item):
                    value = label_item(obj)
                elif hasattr(obj, str(label_item)):
                    attr = getattr(obj, label_item)
                    if callable(attr):
                        value = attr()
                    else:
                        value = attr
                else:
                    raise ValueError('Invalid label_item specified: %s' % str(label_item))
                return value

        return LabelledModelChoiceField

    def get_attrs(self, request, model_admin):
        """Gather the HTML tag attrs from all sources."""
        attrs = self.widget_attrs.copy()
        attrs['id'] = 'id-%s-daaf-filter' % self.parameter_name
        if self.is_placeholder_title:
            # Upper case letter P as dirty hack for bypass django2 widget force placeholder value as empty string ("")
            attrs['data-Placeholder'] = self.title
        return attrs

    @staticmethod
    def get_queryset_for_field(model, name):
        """Determine the appropriate queryset for the filter itself."""
        try:
            field_desc = getattr(model, name)
        except AttributeError:
            field_desc = model._meta.get_field(name)
        if isinstance(field_desc, ManyToManyDescriptor):
            related_model = field_desc.rel.related_model if field_desc.reverse else field_desc.rel.model
        elif isinstance(field_desc, ReverseManyToOneDescriptor):
            related_model = field_desc.rel.related_model  # look at field_desc.related_manager_cls()?
        elif isinstance(field_desc, ForeignObjectRel):
            # includes ManyToOneRel, ManyToManyRel
            # also includes OneToOneRel - not sure how this would be used
            related_model = field_desc.related_model
        else:
            # primarily for ForeignKey/ForeignKeyDeferredAttribute
            # also includes ForwardManyToOneDescriptor, ForwardOneToOneDescriptor, ReverseOneToOneDescriptor
            return field_desc.get_queryset()
        return related_model.objects.get_queryset()

    def get_form_widget(self, request, model_admin):
        """Determine the form widget class to be used."""
        if self.form_widget is not None:
            return self.form_widget
        elif self.multi_select:
            return AutocompleteSelectMultiple
        else:
            return AutocompleteSelect

    def get_widget(self, request, model_admin, remote_field):
        """Create the form widget to be used."""
        widget_class = self.get_form_widget(request, model_admin)
        return widget_class(
            remote_field,
            model_admin.admin_site,
            custom_url=self.get_autocomplete_url(request, model_admin),
        )

    def get_form_field(self, request, model_admin):
        """Determine the form field class to be used."""
        if self.form_field is not None:
            form_field = self.form_field
        elif self.multi_select:
            form_field = ModelMultipleChoiceField
        else:
            form_field = ModelChoiceField
        if self.label_by is not None:
            form_field = self.generate_choice_field(
                self.label_by, form_field, request, model_admin
            )
        return form_field

    def get_field(self, request, model_admin, model, widget):
        """Create the form field to be used."""
        form_field_class = self.get_form_field(request, model_admin)
        return form_field_class(
            queryset=self.get_queryset_for_field(model, self.get_ultimate_field_name()),
            widget=widget,
            required=False,
        )

    def _add_media(self, model_admin, widget):
        """Update the relevant ModelAdmin Media class, creating it if needed."""

        if not hasattr(model_admin, 'Media'):
            model_admin.__class__.Media = type('Media', (object,), dict())
            model_admin.__class__.media = media_property(model_admin.__class__)

        def _get_media(obj):
            return Media(media=getattr(obj, 'Media', None))

        media = _get_media(model_admin) + widget.media + _get_media(AutocompleteFilter) + _get_media(self)

        for name in MEDIA_TYPES:
            setattr(model_admin.Media, name, getattr(media, '_' + name))

    def has_output(self):
        """Indicate that some choices will be output for this filter."""
        return True

    def lookups(self, request, model_admin):
        """Values for rendering. Not used by Select2 widget."""
        return ()

    def prepare_value(self):
        """Prepare the input string value for use."""
        query_parameter = self.get_query_parameter()
        params = self.used_parameters.get(self.parameter_name, '')
        return prepare_lookup_value(query_parameter, params)

    def queryset(self, request, queryset):
        """
        Apply filter to the queryset. Note that distinct() is NOT automatically
        applied, which may result in duplicate values unless applied elsewhere.
        """
        value = self.value()
        if value:
            query_parameter = self.get_query_parameter()
            prepared_value = prepare_lookup_value(query_parameter, value)  # FIXME combine with value() and prepare_value() ?
            return queryset.filter(**{query_parameter: prepared_value})
        else:
            return queryset

    def render_widget(self, field, attrs):
        """Render the widget."""
        prepared_value = self.prepare_value()
        # FIXME check that value is okay before using, make e=1 if not?
        return field.widget.render(
            name=self.parameter_name,
            value=prepared_value,
            attrs=attrs
        )

    def get_autocomplete_url(self, request, model_admin):
        """
        Hook to specify a custom view for autocomplete,
        instead of default django admin's search_results.
        """
        if self.view_name is not None:
            return reverse(self.view_name)
        return None


def AutocompleteFilterFactory(title, field_name, **kwargs):
    """
    An autocomplete widget filter with a customizable title. Use like this:
        * AutocompleteFilterFactory('My title', 'field')
        * AutocompleteFilterFactory('My title', 'fourth__third__second__first')
    Be sure to include distinct() in the model admin get_queryset() if the second form is used.
    Any keyword arguments given are used for AutocompleteFilter class variables.
    """

    # Check for valid kwargs
    attrs = [
        attr for attr in dir(AutocompleteFilter)
        if not callable(getattr(AutocompleteFilter, attr))
        and not attr.startswith('__')
        and attr not in ['title', 'field_name']
    ]
    diff = set(kwargs.keys()) - set(attrs)
    if 'viewname' in kwargs.keys():
        # This check can be removed in a future version
        warnings.warn('The viewname argument is deprecated. Use view_name instead.', DeprecationWarning)
        kwargs.setdefault('view_name', kwargs['viewname'])
        kwargs.pop('viewname')
    elif diff:
        raise ValueError('Invalid argument(s): ' + str(diff))

    # Create new filter class
    base = {'title': title, 'field_name': field_name}
    return AutocompleteFilterMeta(
        'GeneratedAutocompleteFilter',
        (AutocompleteFilter,),
        {**base, **kwargs}
    )