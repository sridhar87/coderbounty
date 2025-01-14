from django.shortcuts import render_to_response, RequestContext, redirect, get_object_or_404
from django.http import HttpResponseNotFound, HttpResponseServerError, HttpResponse
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from models import Issue, UserProfile, Bounty, Service
from .forms import IssueCreateForm, BountyCreateForm
from utils import get_issue, add_issue_to_database, get_twitter_count, get_facebook_count, create_comment, issue_counts, leaderboard, get_hexdigest

from django.views.generic import ListView, DetailView
from django.contrib.auth import get_user_model

from django.shortcuts import get_object_or_404, render

import json
from django.contrib import messages
from django.db.models import Q
import re
import datetime
from django.db.models import Sum, Count
from django.contrib.sites.models import Site
import urllib
import urllib2
import cookielib

from BeautifulSoup import BeautifulSoup
import string
import random
from actstream.models import user_stream
from actstream.models import Action

#implement ajax loader for posting a bounty /post (done)
#auto count for total bounties won
#make circles on homepage links
#integrate avatars
#create browser extension to post bounties from github / bitbucket

#@cache_page(60 * 15)
def parse_url_ajax(request):
    url = request.POST.get('url', '')
    issue = get_issue(request, url)
    return HttpResponse(json.dumps(issue))


def home(request, template="index.html"):
    activities = Action.objects.all()[0:10]
    context = {
        'activities': activities,
        'leaderboard': leaderboard(),
    }
    response = render_to_response(template, context, context_instance=RequestContext(request))
    return response


def create_issue_and_bounty(request):
    languages = []
    for lang in Issue.LANGUAGES:
        languages.append(lang[0])
    user = request.user
    if not user.is_authenticated():
        return render(request, 'post.html', {
            'languages': languages,
            'message': 'You need to be authenticated to post bounty'
        })
    if request.method == 'GET':
        return render(request, 'post.html', {
            'languages': languages,
        })
    if request.method == 'POST':
        url = request.POST.get('issueUrl','')
        if not url:
            messages.error(request, 'Please provide an issue url')
            return render(request, 'post.html', {
                'languages': languages
            })
        issue_data = get_issue(request, url)
        if issue_data:
            service = Service.objects.get(name=issue_data['service'])
            instance = Issue(created = user,number = issue_data['number'],
            project=issue_data['project'],user = user,service=service)
        else:
            return render(request, 'post.html', {
                'languages': languages,
                'message':'Please provide a propper issue url',
            })
        form = IssueCreateForm(request.POST, instance=instance)
        bounty_form = BountyCreateForm(request.POST)
        bounty_form_is_valid = bounty_form.is_valid()
        if form.is_valid() and bounty_form_is_valid:
            issue = form.save()
            price = bounty_form.cleaned_data['price']
            bounty_instance = Bounty(user = user,issue = issue,price = price)
            bounty_instance.save()
            return render(request, 'post.html', {
                'languages': languages,
                'message':'Successfully saved issue'
            })
        else:
            return render(request, 'post.html', {
                'languages': languages,
                'message':'Error',
                'errors': form.errors,
                'bounty_errors':bounty_form.errors,
            })


def list(request):
    q=''
    status=''
    order='-bounty'
    if q:
        entry_query = get_query(q.strip(), ['title', 'content', 'project', 'number', ])
        issues = Issue.objects.filter(entry_query)
    else:
        issues = Issue.objects.all()

    if status and status != "all":
        issues = issues.filter(status=status)

    # if status == "open" or status == '' or status == None:
    #     issues = issues.filter(bounty__ends__gt=datetime.datetime.now())

    if order.find('bounty') > -1:
        issues = issues.annotate(bounty_sum=Sum('bounty__price')).order_by(order + '_sum')

    if order.find('watchers') > -1:
        issues = issues.annotate(watchers_count=Count('watcher')).order_by(order + '_count')

    if order.find('project') > -1:
        issues = issues.annotate(Count('id')).order_by(order)

    if order.find('number') > -1:
        issues = issues.annotate(Count('id')).order_by(order)

    context = {
         'issues': issues,
    }
    response = render_to_response('list.html', context, context_instance=RequestContext(request))
    return response


def issue(request):
    return render(request, 'issue.html')

#@ajax_login_required
def add(request):
    message = ''
    url = ''
    error = ''
    checkout_uri = ''

    if not request.GET.get('bounty', None):
        error = "Please enter a bounty"
    if not request.GET.get('limit', None):
        error = "Please enter a time limit"
    if not request.GET.get('url', None):
        error = "Please enter a Github, Google Code or Bitbucket issue"

    if error:
        if request.is_ajax():
            return HttpResponse(error)
        else:
            messages.error(request, error)
            return redirect('/')

    url = request.GET.get('url', None)
    issue = get_issue(request, url)

    if issue and issue['status'] == "open":
        issue['bounty'] = int(request.GET.get('bounty', 0))
        issue['limit'] = request.GET.get('limit', 0)
        request.session['issue'] = issue
        if request.user.get_profile().balance and (int(request.user.get_profile().balance) > int(request.GET.get('bounty', 0))):
            if add_issue_to_database(request):
                user_profile = UserProfile.objects.get(user=request.user)
                user_profile.balance = int(user_profile.balance) - int(request.GET.get('bounty', 0))
                request.user.get_profile().balance = user_profile.balance
                user_profile.save()
                message = "<span style='color: #8DC63F; font-weight:bold;'>$"\
                    + request.GET.get('bounty', 0) + " <span style='color:#4B4B4B'>bounty added to issue </span><strong>#"\
                    + str(issue['number']) + "</strong>"
                messages.add_message(request, messages.SUCCESS, message)

        else:
            try:
                wepay = WePay(production=settings.IN_PRODUCTION, access_token=settings.WEPAY_ACCESS_TOKEN)

                ctx = {
                'account_id': settings.ACCOUNT_ID,
                'short_description': '$' + request.GET.get('bounty', 0) + " bounty on " + issue['project'] + " issue #" + str(issue['number']),
                'type': 'PERSONAL',
                'amount': int(request.GET.get('bounty', 0)),
                'mode': 'iframe'
                }
                wepay_call_return = wepay.call('/checkout/create', ctx)
                checkout_uri = wepay_call_return['checkout_uri']
                message = "Adding bounty to issue #" + str(issue['number'])
                messages.add_message(request, messages.SUCCESS, message)
            except Exception, e:
                message = "%s" % e

    else:
        if issue['status'] != "open":
            error = "Issue must be open, it is " + issue['status']
            messages.error(request, error)
        storage = messages.get_messages(request)
        for item in storage:
            message += str(item)

    context = {
        'message': message,
        'url': url,
        'issue': issue,
        'wepay_host': settings.DEBUG and "stage" or "www",
        'checkout_uri': checkout_uri,
        'balance': str(request.user.get_profile().balance),
    }

    if request.is_ajax():
        storage = messages.get_messages(request)
        for item in storage:
            message += str(item)
        return HttpResponse(json.dumps(context))

    q = request.GET.get('q', '')
    status = request.GET.get('status', 'open')
    order = request.GET.get('order', '-bounty')
    context['issues'] = get_issues(q, status, order)

    template = "index.html"

    return render_to_response(template, context, context_instance=RequestContext(request))


def wepay_auth(request):
    wepay = WePay(settings.IN_PRODUCTION)
    return redirect(wepay.get_authorization_url("http://" + Site.objects.get(id=settings.SITE_ID).domain + '/wepay_callback', settings.CLIENT_ID))


def wepay_callback(request):
    code = request.GET.get('code')
    wepay = WePay(settings.IN_PRODUCTION)
    response = wepay.get_token("http://" + Site.objects.get(id=settings.SITE_ID).domain + '/wepay_callback', settings.CLIENT_ID, settings.CLIENT_SECRET, code)
    messages.add_message(request, messages.SUCCESS, "Wepay token created !" + response['access_token'])
    return redirect("/")


def post_comment(request):
    issue = Issue.objects.get(number='34')
    create_comment(issue, "Is this still active????")
    return HttpResponse("True")


#@ajax_login_required
def watch(request):
    if request.GET.get('issue', None):

        issue_id = request.GET.get('issue', None)
        bounty = request.GET.get('bounty', False)
        status = request.GET.get('status', False)
        issue = get_object_or_404(Issue, pk=issue_id)

        watcher, created = Watcher.objects.get_or_create(user=request.user, issue=issue)
        watcher.bounty = bounty
        watcher.status = status
        watcher.save()
        if not bounty and not status:
            watcher.delete()

        return HttpResponse(issue.watchers().count())
    else:
        return HttpResponse("False")


def logout_view(request):
    logout(request)
    if request.is_ajax():
        return render_to_response("login_bar.html", {}, context_instance=RequestContext(request))
    return redirect('/')


def error_404_view(request):
    return HttpResponseNotFound('Not Found')


def error_500_view(request):
    return HttpResponseServerError('Internal server error')


def login_async(request):
    username = request.POST.get('username', '')
    password = request.POST.get('password', '')
    if username and password:
        try:
            user = authenticate(username=username, password=password)
            if user is not None:
                if user.is_active:
                    login(request, user)
                    context = {}
                    #if Coin.objects.add_coin_for_user(user, request):
                    #    context.update({'coin_added': 1})
                    return render_to_response("login_bar.html", context, context_instance=RequestContext(request))
                else:
                    return HttpResponse()
        except Exception:
            return HttpResponse()
    return HttpResponse()


def normalize_query(query_string,
                    findterms=re.compile(r'"([^"]+)"|(\S+)').findall,
                    normspace=re.compile(r'\s{2,}').sub):
    return [normspace(' ', (t[0] or t[1]).strip()) for t in findterms(query_string)]


def get_query(query_string, search_fields):
    query = None
    terms = normalize_query(query_string)
    for term in terms:
        or_query = None
        for field_name in search_fields:
            q = Q(**{"%s__icontains" % field_name: term})
            if or_query is None:
                or_query = q
            else:
                or_query = or_query | q
        if query is None:
            query = or_query
        else:
            query = query & or_query
    return query




def join(request):
    if request.method == 'POST':
        if not request.POST.get('agree'):
            return HttpResponse()
        if request.is_ajax():
            #form = UserCreationForm(request.POST)

            if form.is_valid():
                form.save()
                user = authenticate(username=request.POST.get('username'), password=request.POST.get('password1'))
                if user is not None:
                    if user.is_active:
                        user_profile = UserProfile(user=user)
                        user_profile.save()
                        login(request, user)
                        subject = "Welcome to Coder Bounty!"
                        body = "Thank you for joining Coder Bounty! Enjoy the site. http://coderbounty.com"
                        send_mail(subject, body, "Coder Bounty<" + settings.SERVER_EMAIL + ">", [request.POST.get('email')])
                        return render_to_response("login_bar.html", {}, context_instance=RequestContext(request))
                    else:
                        return HttpResponse()

    return HttpResponse()


def verify(request, service_slug):
    username = request.GET.get('username', False)
    verification_code = request.GET.get('verification_code', False)
    if username:
        service_name = ''.join(map(lambda s: s.capitalize() + " ", service_slug.split('-')))
        service = Service.objects.get(name=service_name)

        if verification_code:
            if request.session['random_string'] == verification_code:
                user_service = UserService(user=request.user, service=service, username=username)
                user_service.save()
                return HttpResponse("Thanks for verifying your account. Happy coding!")
            else:
                return HttpResponse("Please enter a valid verification code.")

        random_string = id_generator()
        request.session['random_string'] = random_string

        if service.name == "Bitbucket":
            cj = cookielib.CookieJar()
            opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cj))
            urllib2.install_opener(opener)

            url = urllib2.urlopen('https://bitbucket.org/account/signin/')
            html = url.read()
            doc = BeautifulSoup(html)
            csrf_input = doc.find(attrs=dict(name='csrfmiddlewaretoken'))
            csrf_token = csrf_input['value']

            params = urllib.urlencode(dict(username=settings.BITBUCKET_USERNAME,
                password=settings.BITBUCKET_PASSWORD, csrfmiddlewaretoken=csrf_token, next='/', submit="Log in"))
            req = urllib2.Request('https://bitbucket.org/account/signin/', params)
            req.add_header('Referer', "https://bitbucket.org/account/signin/")
            url = urllib2.urlopen(req)

            url = urllib2.urlopen('https://bitbucket.org/account/notifications/send/?receiver=' + username)
            html = url.read()
            csrf_input = doc.find(attrs=dict(name='csrfmiddlewaretoken'))
            csrf_token = csrf_input['value']

            params = urllib.urlencode(dict(recipient=username, title="Coder Bounty " + service.name + " account verification code",
                message="Your validation code is " + random_string, csrfmiddlewaretoken=csrf_token))
            req = urllib2.Request('https://bitbucket.org/account/notifications/send/', params)
            req.add_header('Referer', 'https://bitbucket.org/account/notifications/send/?receiver=' + username)
            url = urllib2.urlopen(req)

            return HttpResponse("I sent a code to your " + service.name + " account.  Please enter it above and click verify again.")

        else:
            if service.name == "Github":
                url = urllib2.urlopen('https://github.com/' + username)
                html = url.read()
                doc = BeautifulSoup(html)
                msg_button = doc.find('a', "email")
                if msg_button:
                    encoded_email = msg_button['data-email']
                    decoded_email = urllib.unquote(encoded_email)
                else:
                    return HttpResponse("Please add your email address to your public github profile for verification, you can remove it afterwards.")

            elif service.name == "Google Code":
                decoded_email = username

            send_mail("Coder Bounty " + service.name + " account verification code", "Your validation code is " + random_string,
                "Coder Bounty<" + settings.SERVER_EMAIL + ">", [decoded_email])
            return HttpResponse("I sent a code to your " + service.name + " email address.  Please enter it above and click verify again.")
    return HttpResponse("Please enter a username")


def id_generator(size=6, chars=string.ascii_uppercase + string.digits):
    return ''.join(random.choice(chars) for x in range(size))

'''
def profile(request):
    if request.POST:
        user = User.objects.get(id=request.user.id)

        user_profile, created = UserProfile.objects.get_or_create(user=request.user)
        if request.POST.get('payment_paypal', '') == "checked":
            user_profile.payment_service = "paypal"
        else:
            user_profile.payment_service = "wepay"
        user_profile.payment_service_email = request.POST.get('payment_service_email', '')
        save = False
        try:
            if user.username != request.POST.get('username', ''):
                user.username = request.POST.get('username', '')
                save = True
            if user.email != request.POST.get('email', ''):
                user.email = request.POST.get('email', '')
                save = True
            if request.POST.get('password', ''):
                algo = 'sha1'
                salt = get_hexdigest(algo, str(random.random()), str(random.random()))[:5]
                hsh = get_hexdigest(algo, salt, request.POST.get('password', ''))
                user.password = '%s$%s$%s' % (algo, salt, hsh)
                save = True
            if save:
                user.save()

        except Exception, e:
            return HttpResponse("{error}".format(error=e))

        user_profile.save()

        return HttpResponse("Your profile has been saved") 

    return render_to_response("profile.html", context_instance=RequestContext(request))
'''

def profile(request):
    """Redirects to profile page if the requested user is loggedin
    """ 

    try:
        username = request.user.username
    except Exception:
        return redirect('/')

    if username != '':
        profile = '/profile/'+username
        return redirect(profile)
    else:
        return redirect('/')

class UserProfileDetailView(DetailView):
    model = get_user_model()
    slug_field = "username"
    template_name = "profile.html"

    def get_object(self, queryset=None):
        user = super(UserProfileDetailView, self).get_object(queryset)
        UserProfile.objects.get_or_create(user=user)
        return user

    def get_context_data(self, **kwargs):
        context = super(UserProfileDetailView, self).get_context_data(**kwargs)
        context['activities'] = user_stream(self.get_object(), with_user_activity=True)        
        return context


def load_issue(request):
    if request.POST.get('issue[service]'):
        service = Service.objects.get(name=request.POST.get('issue[service]'))
        issue = Issue.objects.get(service=service, number=request.POST.get('issue[number]'), project=request.POST.get('issue[project]'))
        return render_to_response("single_issue.html", {'issue': issue}, context_instance=RequestContext(request))
    return HttpResponse()


#def issue(request, id):
#    issue = Issue.objects.get(id=id)
#    return render_to_response("issue.html", {'issue': issue}, context_instance=RequestContext(request))

def help(request):
    return render_to_response("help.html", context_instance=RequestContext(request))

def terms(request):
    return render_to_response("terms.html", context_instance=RequestContext(request))

def about(request):
    return render_to_response("about.html", context_instance=RequestContext(request))