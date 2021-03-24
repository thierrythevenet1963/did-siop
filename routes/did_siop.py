
import os
import time
from flask import request, session, url_for, Response, abort
from flask import render_template, redirect, jsonify
from authlib.integrations.flask_oauth2 import current_token, client_authenticated
from authlib.oauth2 import OAuth2Error, OAuth2Request
import json
from urllib.parse import urlencode, parse_qs, urlparse, parse_qsl
from urllib import parse
from datetime import datetime, timedelta
import logging
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys import keys
import base64
import secrets

import constante
import oauth2, ns, talao_ipfs
from models import db, User, OAuth2Client
from erc725 import protocol

import logging
logging.basicConfig(level=logging.INFO)

def dict_to_b64(mydict) :
    token_str = json.dumps(mydict)
    token_bytes = token_str.encode()
    token_b64 = base64.b64encode(token_bytes)
    token = token_b64.decode()
    return token

def b64_to_dict(myb64) :
    rtoken_b64 = myb64.encode()
    rtoken_bytes = base64.b64decode(rtoken_b64)
    rtoken_str = rtoken_bytes.decode()
    return json.loads(rtoken_str)

def check_login() :
    """
    check if the user is correctly logged. This function is called everytime a user function is called from Talao website
    """
    if not session.get('username') and not session.get('workspace_contract') :
        logging.error('call abort 403')
        abort(403)
    else :
        return session['username']

# To be rework
def get_resume (workspace_contract, mode) :
    return dict()
"""
def get_resume(workspace_contract, mode) :
    user = Identity(workspace_contract, mode, authenticated=False)
    # clean up Identity to get a resume
    resume = user.__dict__.copy()
    attr_list  = ['synchronous', 'authenticated', 'address', 'workspace_contract','did',
        'other_list', 'education_list', 'experience_list', 'kbis_list', 'certificate_list','skills_list',
        'file_list', 'issuer_keys', 'partners', 'category', 'personal', 'private_key', 'rsa_key', 'picture',
        'signature', 'kyc', 'relay_activated', 'identity_file', 'profil_title', 'type', 'name']
    for attr in attr_list :
        del resume[attr]
    return resume
"""

def current_user():
    if 'id' in session:
        uid = session['id']
        return User.query.get(uid)
    return None

def split_by_crlf(s):
    return [v for v in s.splitlines() if v]

def get_client_workspace(client_id, mode) :
    """
    Client application are found by username
    We know them as they have credentials to access the server
    Client are Talao partners
    """
    client = OAuth2Client.query.filter_by(client_id=client_id).first()
    client_username = json.loads(client._client_metadata)['client_name']
    return ns.get_data_from_username(client_username, mode).get('workspace_contract')

def get_user_workspace(user_id, mode):
    """
    username are workspace contract
    """
    user = User.query.get(user_id)
    return user.username


#@route('/api/v1/oauth_logout')
def oauth_logout():
    post_logout = request.args.get('post_logout_redirect_uri')
    session.clear()
    logging.info('logout ID provider')
    return redirect(post_logout)


def oauth_login(mode):
    """
    @route('/api/v1/oauth_login')
    Identity Provider login FIRST CALL
    Inital call from authorization server redirect
    """
    if not session.get('url') :
        session['url'] = request.args.get('next')
        logging.info('next = %s', session['url'])
    return render_template('login_qrcode.html')


def oauth_login_larger(mode):
    """
    #@route('/api/v1/oauth_login_larger')
    larger QR code
    """
    return render_template('login_mobile.html')

def oauth_wc_login(mode) :
    """
    @app.route('/oauth_wc_login/', methods = ['GET', 'POST'])
    Identity provider login follow up, "IODC confirm screen"
    This functions helps to check if wallet address is an ethereum and if it is an Identity address 
    """
    if request.method == 'GET' :

        wallet_address = request.args.get('wallet_address')
        wallet_name = request.args.get('wallet_name')
        wallet_logo = request.args.get('wallet_logo')

        # if the QR code scan has been refused or wallet address cannot be read we reject
        if 'reject' in  request.args or wallet_address == 'undefined' :
            logging.warning('user rejected QR code')
            return redirect(session['url']+'&reject=on')

        # look for the wallet logo on server if logo is not provided by walletwonnect
        if wallet_logo in ['undefined', None] :
            filename = wallet_name.replace(' ', '').lower()
            wallet_logo = "/static/img/wallet/" + filename + ".png"

        # clean up address for malformed walletconnect data
        wallet_address = mode.w3.toChecksumAddress(wallet_address)
        session['wallet_address'] = wallet_address

        # check if wallet account is an owner or alias
        workspace_contract = protocol.ownersToContracts(wallet_address, mode)
        if not workspace_contract or workspace_contract == '0x0000000000000000000000000000000000000000' :
            # This wallet address is not an Identity owner, lets check if it is an alias (mode workspace = wallet used only for login)
            wallet_username = ns.get_username_from_wallet(wallet_address, mode)
            if not wallet_username :
                # This wallet addresss is not an alias, lest rejest and propose a new registration
                logging.warning('This wallet account is not an Identity owner')
                return render_template('wc_reject.html', wallet_address=wallet_address)
            else :
                logging.info('This wallet is an Alias')
                # This wallet is an alias, we look for the workspace_contract attached
                workspace_contract = ns.get_data_from_username(wallet_username, mode)['workspace_contract']

        session['workspace_contract'] = workspace_contract
        logging.info("This wallet account is owner of  = %s", workspace_contract)
        client_request = dict(parse.parse_qsl(parse.urlsplit(session['url']).query))
        # confirm.htlm is a dapp which is going to check the client request in regard of its did and signature
        # and provide a user signature to a code sent by the authorization server

        return render_template('did_oidc_confirm.html',
                                **client_request,
								wallet_address=wallet_address,
								wallet_name = wallet_name,
								wallet_logo= wallet_logo,)

    if request.method == 'POST' :

        # register user in local base
        user = User.query.filter_by(username=session['workspace_contract']).first()
        if not user:
            user = User(username=workspace_contract)
            db.session.add(user)
            db.session.commit()
        session['id'] = user.id

        # return to authorization server for user consent
        return redirect(session['url'] + '&wallet_address=' + session['wallet_address'])


#@route('/oauth/revoke', methods=['POST'])
def revoke_token():
    return oauth2.authorization.create_endpoint_response('revocation')

#@route('/api/v1/oauth/token', methods=['POST'])
def issue_token():
    response = oauth2.authorization.create_token_response()
    return response

def authorize(mode):
    """
    @route('/api/v1/authorize', methods=['GET', 'POST'])
    Authorization server modifed for  DID-SIOP

    """
    # to manage wrong login ot user rejection, qr code exit, any other reject 
    if 'reject' in request.values :
        logging.warning('reject')
        session.clear()
        return oauth2.authorization.create_authorization_response(grant_user=None)

    # get client Identity from API credentials
    user = current_user()
    client_id = request.args.get('client_id')
    client = OAuth2Client.query.filter_by(client_id=client_id).first()

    # if user not logged (Auth server), then to log it in
    if not user :
        logging.info('user not registered')
        return redirect(url_for('oauth_login', next=request.url))

    # if user is already logged we check the request and prepare the "OIDC consent screen"
    if request.method == 'GET' :
        try:
            grant = oauth2.authorization.validate_consent_request(end_user=user)
        except OAuth2Error as error:
            logging.error('OAuth2Error')
            return jsonify(dict(error.get_body()))

        # configure consent screen : authorize.html
        consent_screen_scopes = ['openid', 'address', 'profile', 'about', 'birthdate', 'resume', 'proof_of_identity', 'email', 'phone', 'did_authn']
        checkbox = {key.replace(':', '_') : 'checked' if key in grant.request.scope.split() and key in client.scope.split() else ""  for key in consent_screen_scopes}
        return render_template('did_oidc_authorize.html', **checkbox,)

    # POST, call from consent view  authorize.html

    # get credential from wallet
    credential = request.form['credential']
    did = request.form['did']

    # Verify signature of Identity with eth_sign method
    try :
        msg = encode_defunct(text= credential)
        signer = Account.recover_message(msg, signature=request.form['signature'])
    except :
        logging.error('signature invalid')
        return oauth2.authorization.create_authorization_response(grant_user=None)

    logging.info('signer =  %s', signer)
    if signer != protocol.contractsToOwners('0x' + did.split(':')[3], mode) :
        logging.error('signer is not wallet address, login rejected')
        return oauth2.authorization.create_authorization_response(grant_user=None)

    try :
        ns.add_vc(did, credential)
    except :
        logging.error('database locked, credential is not stored')

    # update scopes after user consent
    query_dict = parse_qs(request.query_string.decode("utf-8"))
    my_scope = ""
    for scope in query_dict['scope'][0].split() :
        if request.form.get(scope) :
            my_scope = my_scope + scope + " "
    query_dict["scope"] = [my_scope[:-1]]

    # create a Oauth2Request with the updated scope in the query_dict
    req = OAuth2Request("POST", request.base_url + "?" + urlencode(query_dict, doseq=True))

    # send response
    return oauth2.authorization.create_authorization_response(grant_user=user, request=req)


# endpoint standard OIDC
#route('/api/v1/user_info')
@oauth2.require_oauth('address openid profile resume email birthdate proof_of_identity about resume gender name contact_phone website', 'OR')
def user_info(mode):
    user_id = current_token.user_id
    user_workspace_contract = get_user_workspace(user_id,mode)
    user_info = dict()
    did = 'did:talao:' + mode.BLOCKCHAIN +':' + user_workspace_contract[2:]
    user_info['sub'] = did
    user_info['credential'] = json.dumps(json.loads(ns.get_vc(did)[0])["did_authn"])
    # credential is deleted
    try :
        ns.del_vc(user_info['sub'])
    except :
        logging.error('credential deletion failed')
    # setup response
    response = Response(json.dumps(user_info), status=200, mimetype='application/json')
    return response

