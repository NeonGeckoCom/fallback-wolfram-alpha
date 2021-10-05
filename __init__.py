# NEON AI (TM) SOFTWARE, Software Development Kit & Application Development System
#
# Copyright 2008-2021 Neongecko.com Inc. | All Rights Reserved
#
# Notice of License - Duplicating this Notice of License near the start of any file containing
# a derivative of this software is a condition of license for this software.
# Friendly Licensing:
# No charge, open source royalty free use of the Neon AI software source and object is offered for
# educational users, noncommercial enthusiasts, Public Benefit Corporations (and LLCs) and
# Social Purpose Corporations (and LLCs). Developers can contact developers@neon.ai
# For commercial licensing, distribution of derivative works or redistribution please contact licenses@neon.ai
# Distributed on an "AS IS” basis without warranties or conditions of any kind, either express or implied.
# Trademarks of Neongecko: Neon AI(TM), Neon Assist (TM), Neon Communicator(TM), Klat(TM)
# Authors: Guy Daniels, Daniel McKnight, Regina Bloomstine, Elon Gasper, Richard Leeds
#
# Specialized conversational reconveyance options from Conversation Processing Intelligence Corp.
# US Patents 2008-2021: US7424516, US20140161250, US20140177813, US8638908, US8068604, US8553852, US10530923, US10530924
# China Patent: CN102017585  -  Europe Patent: EU2156652  -  Patents Pending
#
# This software is an enhanced derivation of the Mycroft Project which is licensed under the
# Apache software Foundation software license 2.0 https://www.apache.org/licenses/LICENSE-2.0
# Changes Copyright 2008-2021 Neongecko.com Inc. | All Rights Reserved
#
# Copyright 2017 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re

from adapt.intent import IntentBuilder
from neon_utils import get_utterance_user
from neon_utils.skills.common_query_skill import CommonQuerySkill, CQSMatchLevel
from neon_utils.logger import LOG
from neon_utils.authentication_utils import find_neon_wolfram_key
from neon_utils.service_apis.wolfram_alpha import get_wolfram_alpha_response, QueryApi

from mycroft.util.parse import normalize


class EnglishQuestionParser(object):
    """
    Poor-man's english question parser. Not even close to conclusive, but
    appears to construct some decent w|a queries and responses.
    """

    def __init__(self):
        self.regexes = [
            # Match things like:
            #    * when X was Y, e.g. "tell me when america was founded"
            #    how X is Y, e.g. "how tall is mount everest"
            re.compile(
                ".*(?P<QuestionWord>who|what|when|where|why|which|whose|convert|how old) "
                "(?P<Query1>.*) (?P<QuestionVerb>is|are|was|were|to) "
                "(?P<Query2>.*)"),
            # Match:
            #    how X Y, e.g. "how do crickets chirp"
            re.compile(
                ".*(?P<QuestionWord>who|what|when|where|why|which|how) "
                "(?P<QuestionVerb>\w+) (?P<Query>.*)")
        ]

    @staticmethod
    def _normalize(groupdict):
        if "Query" in groupdict:
            return groupdict
        elif "Query1" and "Query2" in groupdict:
            # Join the two parts into a single 'Query'
            return {
                "QuestionWord": groupdict.get("QuestionWord"),
                "QuestionVerb": groupdict.get("QuestionVerb"),
                "Query": " ".join([groupdict.get("Query1"), groupdict.get("Query2")]),
            }

    def parse(self, utterance):
        for regex in self.regexes:
            match = regex.match(utterance)
            if match:
                return self._normalize(match.groupdict())
        return None


class WolframAlphaSkill(CommonQuerySkill):
    PIDS = [
        "Value",
        "NotableFacts:PeopleData",
        "BasicInformation:PeopleData",
        "Definition",
        "DecimalApproximation",
    ]

    def __init__(self):
        super().__init__()
        self.question_parser = EnglishQuestionParser()
        self.queries = {}
        self.saved_answers = self.get_cached_data("wolfram.cache")
        self.appID = None
        self._get_app_id()

    def _get_app_id(self):
        if check_wolfram_credentials(self.settings.get("appId")):
            self.appID = self.settings.get("appId")
        elif check_wolfram_credentials(self.local_config.get("authVars", {}).get("waID")):
            self.appID = self.local_config.get("authVars", {}).get("waID")
        else:
            try:
                self.appID = find_neon_wolfram_key()
            except FileNotFoundError:
                self.appID = None

    def initialize(self):
        sources_intent = IntentBuilder("WolframSource").require("Give").require("Source").build()
        self.register_intent(sources_intent, self.handle_get_sources)

        ask_wolfram_intent = IntentBuilder("AskWolfram").require("Request").build()
        self.register_intent(ask_wolfram_intent, self.handle_ask_wolfram)

    def handle_ask_wolfram(self, message):
        utterance = message.data.get("utterance").replace(message.data.get("Request"), "")
        user = get_utterance_user(message)
        result, _ = self._query_wolfram(utterance, message)
        if result:
            self.speak_dialog("response", {"response": result.rstrip('.')})
            self.queries[user] = utterance
            if self.gui_enabled:
                url = 'https://www.wolframalpha.com/input?i=' + utterance.replace(' ', '+')
                self.gui.show_url(url)
                self.clear_gui_timeout(120)

    def CQS_match_query_phrase(self, utt, message):
        LOG.info(utt)
        result, key = self._query_wolfram(utt, message)
        if result:
            to_speak = self.dialog_renderer.render("response", {"response": result.rstrip(".")})
            user = self.get_utterance_user(message)
            return utt, CQSMatchLevel.GENERAL, to_speak, {"query": utt, "answer": result,
                                                          "user": user, "key": key}
        else:
            return None

    def CQS_action(self, phrase, data):
        """ If selected prepare to send sources. """
        if data:
            LOG.info('Setting information for source')
            user = data['user']
            self.queries[user] = data["query"]
            if self.gui_enabled:
                url = 'https://www.wolframalpha.com/input?i=' + data["query"].replace(' ', '+')
                self.gui.show_url(url)
                self.clear_gui_timeout(120)

    def handle_get_sources(self, message):
        user = self.get_utterance_user(message)
        if user in self.queries.keys():
            last_query = self.queries[user]
            preference_user = self.preference_user(message)
            email_addr = preference_user["email"]

            if email_addr:
                title = "Wolfram|Alpha Source"
                body = f"\nHere is the answer to your question: {last_query}\nView result on " \
                       f"Wolfram|Alpha: https://www.wolframalpha.com/input/?i={last_query.replace(' ', '+')}\n\n" \
                       f"-Neon"
                # Send Email
                self.send_email(title, body, message, email_addr)
                self.speak_dialog("sent.email", {"email": email_addr}, private=True)
            else:
                self.speak_dialog("no.email", private=True)
        else:
            self.speak_dialog("no.info.to.send", private=True)

    def stop(self):
        if self.gui_enabled:
            self.gui.clear()

    def _query_wolfram(self, utterance, message):
        utterance = normalize(utterance, remove_articles=False)
        parsed_question = self.question_parser.parse(utterance)
        LOG.debug(parsed_question)
        if parsed_question:
            # Try to store pieces of utterance (None if not parsed_question)
            utt_word = parsed_question.get('QuestionWord')
            utt_verb = parsed_question.get('QuestionVerb')
            utt_query = parsed_question.get('Query')
            LOG.debug(len(str(utt_query).split()))
            query = "%s %s %s" % (utt_word, utt_verb, utt_query)
            LOG.debug("Querying WolframAlpha: " + query)

            preference_location = self.preference_location(message)
            lat = str(preference_location['lat'])
            lng = str(preference_location['lng'])
            units = str(self.preference_unit(message)["measure"])
            query_type = QueryApi.SHORT if self.server else QueryApi.SPOKEN
            key = (utterance, lat, lng, units, repr(query_type))

            # TODO: This should be its own intent or skill DM
            if "convert" in query:
                to_convert = utt_query[:utt_query.index(utt_query.split(" ")[-1])]
                query = f'convert {to_convert} to {query.split("to")[1].split(" ")[-1]}'
            LOG.info(f"query={query}")

            if self.saved_answers.get(key):
                LOG.info(f"Using W|A Cached response")
                result = self.saved_answers.get(key)[0]
            else:
                kwargs = {"lat": lat, "lng": lng}
                if self.appID:
                    kwargs["app_id"] = self.appID
                result = get_wolfram_alpha_response(query, query_type, units, **kwargs)
                LOG.info(f"result={result}")
            if result:
                self.saved_answers[key] = [result, query]
                self.update_cached_data("wolfram.txt", self.saved_answers)
        else:
            result = None
            key = None
        return result, key


def check_wolfram_credentials(cred_str) -> bool:
    """
    Validate a WolframAlpha credential
    :param cred_str: string appID to test
    :return: True if credential is valid, else False
    """
    import requests
    if not cred_str:
        return False
    try:
        url = f'https://api.wolframalpha.com/v2/result?appid={cred_str}&i=who+are+you'
        resp = requests.get(url)
        return resp.ok
    except Exception as e:
        LOG.error(e)
        return False


def create_skill():
    return WolframAlphaSkill()
