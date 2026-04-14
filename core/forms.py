from django import forms
from django.contrib.auth.models import User
import re
from .models import Location, Contact, EmailBlast


class LocationTypeSelectMultiple(forms.SelectMultiple):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.location_type_map = {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        key = str(value) if value is not None else ''
        loc_type = self.location_type_map.get(key)
        if loc_type:
            option.setdefault('attrs', {})
            option['attrs']['data-type'] = loc_type
        return option


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        # FIXED: Explicit fields list
        fields = ['name', 'type', 'status', 'address', 'city', 'state', 'zip_code', 'county', 'contact_name', 'contact_title', 'phone', 'email', 'website', 'facebook', 'instagram', 'twitter', 'social_link', 'capacity', 'amenities', 'description', 'notes', 'latitude', 'longitude']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Location name'}),
            'type': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control'}),
            'zip_code': forms.TextInput(attrs={'class': 'form-control'}),
            'county': forms.TextInput(attrs={'class': 'form-control'}),
            'contact_name': forms.TextInput(attrs={'class': 'form-control'}),
            'contact_title': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'website': forms.URLInput(attrs={'class': 'form-control'}),
            'facebook': forms.URLInput(attrs={'class': 'form-control'}),
            'instagram': forms.URLInput(attrs={'class': 'form-control'}),
            'twitter': forms.URLInput(attrs={'class': 'form-control'}),
            'social_link': forms.URLInput(attrs={'class': 'form-control'}),
            'capacity': forms.NumberInput(attrs={'class': 'form-control'}),
            'amenities': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        if email:
            return email.lower().strip()
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '')
        if phone:
            return re.sub(r'\D', '', phone)
        return phone


class ContactForm(forms.ModelForm):
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        empty_label='Select Location',
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_location'})
    )

    class Meta:
        model = Contact
        # FIXED: Explicit fields list
        fields = ['first_name', 'last_name', 'email', 'phone', 'gender', 'age', 'city', 'state', 'zip_code', 'status', 'notes', 'is_subscribed', 'location']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'gender': forms.Select(attrs={'class': 'form-select'}),
            'age': forms.NumberInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'state': forms.TextInput(attrs={'class': 'form-control'}),
            'zip_code': forms.TextInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_subscribed': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        if email:
            return email.lower().strip()
        return email

    def clean_phone(self):
        # FIXED: phone field — strip non-numeric characters in clean_phone()
        phone = self.cleaned_data.get('phone', '')
        if phone:
            return re.sub(r'\D', '', phone)
        return phone


class EmailBlastForm(forms.ModelForm):
    target_locations = forms.ModelMultipleChoiceField(
        queryset=Location.objects.all(),
        required=False,
        widget=LocationTypeSelectMultiple(attrs={'class': 'form-select', 'size': 12, 'id': 'id_target_locations'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['body_text'].required = True
        qs = Location.objects.all().only('id', 'type')
        widget = self.fields['target_locations'].widget
        if isinstance(widget, LocationTypeSelectMultiple):
            widget.location_type_map = {str(loc.pk): loc.type for loc in qs}

    class Meta:
        model = EmailBlast
        fields = ['subject', 'body_text', 'recipient_type', 'target_locations', 'target_contacts', 'scheduled_at']
        widgets = {
            'subject': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Email subject...'}),
            'body_text': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
            'recipient_type': forms.Select(
                choices=[('all', 'All Contacts'), ('location', 'By Location'), ('custom', 'Custom Selection')],
                attrs={'class': 'form-select'}
            ),
            'target_contacts': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 6}),
            'scheduled_at': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
        }


class RegisterForm(forms.Form):
    first_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'}))
    last_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email Address'}))
    password = forms.CharField(min_length=8, widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Password (min 8 chars)'}))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm Password'}))

    def clean(self):
        cleaned = super().clean()
        email = (cleaned.get('email') or '').strip().lower() # FIXED: email lowercased and stripped
        cleaned['email'] = email
        if cleaned.get('password') != cleaned.get('confirm_password'):
            raise forms.ValidationError("Passwords do not match.")
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return cleaned


# FIXED: OTP form to handle validation of code (6 digits) and email stripping
class OTPVerifyForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control'}))
    otp_code = forms.CharField(max_length=6, min_length=6, widget=forms.TextInput(attrs={
        'class': 'form-control text-center',
        'style': 'letter-spacing:8px;font-size:1.5rem;font-weight:700;',
        'maxlength': '6',
        'inputmode': 'numeric',
    }))
    purpose = forms.CharField(max_length=20, initial='login', widget=forms.HiddenInput())

    def clean(self):
        cleaned = super().clean()
        email = (cleaned.get('email') or '').strip().lower()
        cleaned['email'] = email
        otp_code = cleaned.get('otp_code', '')
        if not otp_code.isdigit() or len(otp_code) != 6:
            raise forms.ValidationError("OTP code must be exactly 6 digits.")
        return cleaned


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'you@example.com'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': '********'}))

    def clean_email(self):
        return (self.cleaned_data.get('email') or '').strip().lower()


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'you@example.com'}))

    def clean_email(self):
        return (self.cleaned_data.get('email') or '').strip().lower()


class PasswordResetConfirmForm(forms.Form):
    new_password = forms.CharField(min_length=8, widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'New Password'}))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm New Password'}))

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password')
        if password:
            if len(password) < 8:
                raise forms.ValidationError("Password must be at least 8 characters.")
            if not re.search(r'[A-Z]', password):
                raise forms.ValidationError("Password must contain at least one uppercase letter.")
            if not re.search(r'[0-9]', password):
                raise forms.ValidationError("Password must contain at least one digit.")
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('new_password') != cleaned.get('confirm_password'):
            raise forms.ValidationError("Passwords do not match.")
        return cleaned


class ImportForm(forms.Form):
    file = forms.FileField(
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.xls,.csv'}),
        help_text="Upload .xlsx, .xls or .csv file"
    )
    import_type = forms.ChoiceField(
        choices=[
            ('auto', 'Auto Detect (Excel multi-sheet)'),
            ('location', 'Locations'),
            ('contact', 'Contacts'),
            ('both', 'Locations + Contacts'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )


# ─── Email Template Form ─────────────────────────────────────────
from .models import EmailTemplate, SocialPlatformConfig, SMSConfig, SMSBlast

class EmailTemplateForm(forms.ModelForm):
    class Meta:
        model = EmailTemplate
        fields = ['name', 'category', 'subject', 'body', 'from_name']
        widgets = {
            'name':      forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Template name'}),
            'category':  forms.Select(attrs={'class': 'form-select'}),
            'subject':   forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Email subject line...'}),
            'body':      forms.Textarea(attrs={'class': 'form-control', 'rows': 14, 'id': 'tplBody'}),
            'from_name': forms.TextInput(attrs={'class': 'form-control'}),
        }


class SocialPlatformConfigForm(forms.ModelForm):
    class Meta:
        model = SocialPlatformConfig
        fields = ['account_name', 'access_token', 'app_id', 'app_secret', 'extra_field', 'is_connected']
        widgets = {
            'account_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Main Page, Events Account'}),
            'access_token': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Paste access token...'}),
            'app_id':       forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'App ID / Client ID'}),
            'app_secret':   forms.PasswordInput(render_value=True, attrs={'class': 'form-control', 'placeholder': 'App Secret'}),
            'extra_field':  forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Page ID / Phone No.'}),
            'is_connected': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class SocialPostForm(forms.Form):
    platforms   = forms.MultipleChoiceField(
        choices=[('facebook','Facebook'),('instagram','Instagram'),('twitter','X (Twitter)'),
                 ('linkedin','LinkedIn'),('threads','Threads'),('pinterest','Pinterest')],
        widget=forms.CheckboxSelectMultiple,
        required=True,
    )
    caption     = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4}))
    location_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    schedule_at = forms.DateTimeField(required=False, widget=forms.DateTimeInput(
        attrs={'class': 'form-control', 'type': 'datetime-local'}))


class SMSConfigForm(forms.ModelForm):
    class Meta:
        model = SMSConfig
        fields = ['api_key', 'api_secret', 'from_number', 'is_active']
        widgets = {
            'api_key':     forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'API Key / Account SID'}),
            'api_secret':  forms.PasswordInput(render_value=True, attrs={'class': 'form-control', 'placeholder': 'Auth Token / API Secret'}),
            'from_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+1xxxxxxxxxx'}),
            'is_active':   forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class SMSBlastForm(forms.ModelForm):
    class Meta:
        model = SMSBlast
        fields = ['message', 'recipient_type', 'target_city', 'custom_numbers', 'scheduled_at']
        widgets = {
            'message':        forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'maxlength': 160}),
            'recipient_type': forms.Select(
                choices=[('all','All Subscribed Contacts'),('city','Filter by City'),('custom','Custom Numbers')],
                attrs={'class': 'form-select'}),
            'target_city':    forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'City name'}),
            'custom_numbers': forms.Textarea(attrs={'class': 'form-control', 'rows': 3,
                                                    'placeholder': '+13055551234, +19545557890, ...'}),
            'scheduled_at':   forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
        }



