/* Passwords go directly from the browser to the configured managed provider. */
'use strict';
window.qaIdentity = {
  config: null,
  async provider(action, body) {
    const key = this.config?.web_api_key;
    if (!key) throw new Error('Managed sign-in is not configured.');
    const res = await fetch(`https://identitytoolkit.googleapis.com/v1/accounts:${action}?key=${encodeURIComponent(key)}`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), credentials:'omit', referrerPolicy:'no-referrer'
    });
    if (!res.ok) throw new Error('The identity request failed. Check your details or try again later.');
    return res.json();
  },
  async signIn(email,password) {
    const identity = await this.provider('signInWithPassword',{email,password,returnSecureToken:true});
    // Keep the short-lived ID token in memory only, then discard it after exchange.
    const res = await fetch('/api/session',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-QA-Request':'1'},body:JSON.stringify({id_token:identity.idToken})});
    if (!res.ok) {const data=await res.json();throw new Error(data.detail || 'Sign-in failed.');}
  },
  async register(email,password) {
    const identity=await this.provider('signUp',{email,password,returnSecureToken:true});
    await this.provider('sendOobCode',{requestType:'VERIFY_EMAIL',idToken:identity.idToken});
    return 'Check your email to verify your account, then sign in to request QA membership.';
  },
  async reset(email) {
    await this.provider('sendOobCode',{requestType:'PASSWORD_RESET',email});
    return 'If the account is eligible, a reset email will arrive.';
  }
};
