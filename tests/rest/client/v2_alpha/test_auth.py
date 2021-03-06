# -*- coding: utf-8 -*-
# Copyright 2018 New Vector
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from twisted.internet.defer import succeed

import synapse.rest.admin
from synapse.api.constants import LoginType
from synapse.handlers.ui_auth.checkers import UserInteractiveAuthChecker
from synapse.rest.client.v2_alpha import auth, register

from tests import unittest


class DummyRecaptchaChecker(UserInteractiveAuthChecker):
    def __init__(self, hs):
        super().__init__(hs)
        self.recaptcha_attempts = []

    def check_auth(self, authdict, clientip):
        self.recaptcha_attempts.append((authdict, clientip))
        return succeed(True)


class FallbackAuthTests(unittest.HomeserverTestCase):

    servlets = [
        auth.register_servlets,
        synapse.rest.admin.register_servlets_for_client_rest_resource,
        register.register_servlets,
    ]
    hijack_auth = False

    def make_homeserver(self, reactor, clock):

        config = self.default_config()

        config["enable_registration_captcha"] = True
        config["recaptcha_public_key"] = "brokencake"
        config["registrations_require_3pid"] = []

        hs = self.setup_test_homeserver(config=config)
        return hs

    def prepare(self, reactor, clock, hs):
        self.recaptcha_checker = DummyRecaptchaChecker(hs)
        auth_handler = hs.get_auth_handler()
        auth_handler.checkers[LoginType.RECAPTCHA] = self.recaptcha_checker

    @unittest.INFO
    def test_fallback_captcha(self):

        request, channel = self.make_request(
            "POST",
            "register",
            {"username": "user", "type": "m.login.password", "password": "bar"},
        )
        self.render(request)

        # Returns a 401 as per the spec
        self.assertEqual(request.code, 401)
        # Grab the session
        session = channel.json_body["session"]
        # Assert our configured public key is being given
        self.assertEqual(
            channel.json_body["params"]["m.login.recaptcha"]["public_key"], "brokencake"
        )

        request, channel = self.make_request(
            "GET", "auth/m.login.recaptcha/fallback/web?session=" + session
        )
        self.render(request)
        self.assertEqual(request.code, 200)

        request, channel = self.make_request(
            "POST",
            "auth/m.login.recaptcha/fallback/web?session="
            + session
            + "&g-recaptcha-response=a",
        )
        self.render(request)
        self.assertEqual(request.code, 200)

        # The recaptcha handler is called with the response given
        attempts = self.recaptcha_checker.recaptcha_attempts
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0][0]["response"], "a")

        # also complete the dummy auth
        request, channel = self.make_request(
            "POST", "register", {"auth": {"session": session, "type": "m.login.dummy"}}
        )
        self.render(request)

        # Now we should have fulfilled a complete auth flow, including
        # the recaptcha fallback step, we can then send a
        # request to the register API with the session in the authdict.
        request, channel = self.make_request(
            "POST", "register", {"auth": {"session": session}}
        )
        self.render(request)
        self.assertEqual(channel.code, 200)

        # We're given a registered user.
        self.assertEqual(channel.json_body["user_id"], "@user:test")

    def test_cannot_change_operation(self):
        """
        The initial requested operation cannot be modified during the user interactive authentication session.
        """

        # Make the initial request to register. (Later on a different password
        # will be used.)
        request, channel = self.make_request(
            "POST",
            "register",
            {"username": "user", "type": "m.login.password", "password": "bar"},
        )
        self.render(request)

        # Returns a 401 as per the spec
        self.assertEqual(request.code, 401)
        # Grab the session
        session = channel.json_body["session"]
        # Assert our configured public key is being given
        self.assertEqual(
            channel.json_body["params"]["m.login.recaptcha"]["public_key"], "brokencake"
        )

        request, channel = self.make_request(
            "GET", "auth/m.login.recaptcha/fallback/web?session=" + session
        )
        self.render(request)
        self.assertEqual(request.code, 200)

        request, channel = self.make_request(
            "POST",
            "auth/m.login.recaptcha/fallback/web?session="
            + session
            + "&g-recaptcha-response=a",
        )
        self.render(request)
        self.assertEqual(request.code, 200)

        # The recaptcha handler is called with the response given
        attempts = self.recaptcha_checker.recaptcha_attempts
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0][0]["response"], "a")

        # also complete the dummy auth
        request, channel = self.make_request(
            "POST", "register", {"auth": {"session": session, "type": "m.login.dummy"}}
        )
        self.render(request)

        # Now we should have fulfilled a complete auth flow, including
        # the recaptcha fallback step. Make the initial request again, but
        # with a different password. This causes the request to fail since the
        # operaiton was modified during the ui auth session.
        request, channel = self.make_request(
            "POST",
            "register",
            {
                "username": "user",
                "type": "m.login.password",
                "password": "foo",  # Note this doesn't match the original request.
                "auth": {"session": session},
            },
        )
        self.render(request)
        self.assertEqual(channel.code, 403)

    def test_complete_operation_unknown_session(self):
        """
        Attempting to mark an invalid session as complete should error.
        """

        # Make the initial request to register. (Later on a different password
        # will be used.)
        request, channel = self.make_request(
            "POST",
            "register",
            {"username": "user", "type": "m.login.password", "password": "bar"},
        )
        self.render(request)

        # Returns a 401 as per the spec
        self.assertEqual(request.code, 401)
        # Grab the session
        session = channel.json_body["session"]
        # Assert our configured public key is being given
        self.assertEqual(
            channel.json_body["params"]["m.login.recaptcha"]["public_key"], "brokencake"
        )

        request, channel = self.make_request(
            "GET", "auth/m.login.recaptcha/fallback/web?session=" + session
        )
        self.render(request)
        self.assertEqual(request.code, 200)

        # Attempt to complete an unknown session, which should return an error.
        unknown_session = session + "unknown"
        request, channel = self.make_request(
            "POST",
            "auth/m.login.recaptcha/fallback/web?session="
            + unknown_session
            + "&g-recaptcha-response=a",
        )
        self.render(request)
        self.assertEqual(request.code, 400)
