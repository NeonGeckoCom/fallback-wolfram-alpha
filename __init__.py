# NEON AI (TM) SOFTWARE, Software Development Kit & Application Framework
# All trademark and other rights reserved by their respective owners
# Copyright 2008-2022 Neongecko.com Inc.
# Contributors: Daniel McKnight, Guy Daniels, Elon Gasper, Richard Leeds,
# Regina Bloomstine, Casimiro Ferreira, Andrii Pernatii, Kirill Hrymailo
# BSD-3 License
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from this
#    software without specific prior written permission.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS  BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
# OR PROFITS;  OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE,  EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
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
from ovos_utils import classproperty
from ovos_utils.log import LOG
from ovos_utils.process_utils import RuntimeRequirements
from lingua_franca.parse import normalize
from neon_utils.skills.common_query_skill import CommonQuerySkill, CQSMatchLevel
from neon_utils.user_utils import get_message_user, get_user_prefs
from neon_utils.hana_utils import request_backend


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
    def __init__(self, **kwargs):
        CommonQuerySkill.__init__(self, **kwargs)
        self.question_parser = EnglishQuestionParser()
        self.queries = {}

    @classproperty
    def runtime_requirements(self):
        return RuntimeRequirements(network_before_load=False,
                                   internet_before_load=False,
                                   gui_before_load=False,
                                   requires_internet=True,
                                   requires_network=True,
                                   requires_gui=False,
                                   no_internet_fallback=False,
                                   no_network_fallback=False,
                                   no_gui_fallback=True)

    def initialize(self):
        sources_intent = IntentBuilder("WolframSource").require("Give").require("Source").build()
        self.register_intent(sources_intent, self.handle_get_sources)

        ask_wolfram_intent = IntentBuilder("AskWolfram").require("Request").build()
        self.register_intent(ask_wolfram_intent, self.handle_ask_wolfram)

    def handle_ask_wolfram(self, message):
        utterance = message.data.get("utterance")\
            .replace(message.data.get("Request"), "")
        user = get_message_user(message)
        result, _ = self._query_wolfram(utterance, message)
        if result:
            self.speak_dialog("response", {"response": result.rstrip('.')})
            self.queries[user] = utterance
            url = 'https://www.wolframalpha.com/input?i=' + \
                  utterance.replace(' ', '+')
            self.gui.show_url(url)

    def CQS_match_query_phrase(self, utt, message):
        LOG.info(utt)
        result, key = self._query_wolfram(utt, message)
        if result:
            to_speak = self.dialog_renderer.render(
                "response", {"response": result.rstrip(".")})
            user = get_message_user(message)
            return utt, CQSMatchLevel.GENERAL, to_speak,\
                {"query": utt, "answer": result, "user": user, "key": key}
        else:
            return None

    def CQS_action(self, phrase, data):
        """ If selected prepare to send sources. """
        if data:
            LOG.info('Setting information for source')
            user = data['user']
            self.queries[user] = data["query"]
            if self.gui_enabled:
                url = 'https://www.wolframalpha.com/input?i=' + \
                      data["query"].replace(' ', '+')
                self.gui.show_url(url)

    def handle_get_sources(self, message):
        user = get_message_user(message)
        if user in self.queries.keys():
            last_query = self.queries[user]
            preference_user = get_user_prefs(message)["user"]
            email_addr = preference_user["email"]

            if email_addr:
                title = "Wolfram|Alpha Source"
                body = f"\nHere is the answer to your question: " \
                       f"{last_query}\nView result on Wolfram|Alpha: " \
                       f"https://www.wolframalpha.com/input/?i=" \
                       f"{last_query.replace(' ', '+')}\n\n" \
                       f"-Neon"
                # Send Email
                self.send_email(title, body, message, email_addr)
                self.speak_dialog("sent.email", {"email": email_addr},
                                  private=True)
            else:
                self.speak_dialog("no.email", private=True)
        else:
            self.speak_dialog("no.info.to.send", private=True)

    def stop(self):
        if self.gui_enabled:
            self.gui.clear()

    def _query_wolfram(self, utterance, message) -> tuple:
        utterance = normalize(utterance, remove_articles=False)
        parsed_question = self.question_parser.parse(utterance)
        LOG.debug(parsed_question)
        if not parsed_question:
            return None, None

        # Try to store pieces of utterance (None if not parsed_question)
        utt_word = parsed_question.get('QuestionWord')
        utt_verb = parsed_question.get('QuestionVerb')
        utt_query = parsed_question.get('Query')
        LOG.debug(len(str(utt_query).split()))
        query = "%s %s %s" % (utt_word, utt_verb, utt_query)
        LOG.debug("Querying WolframAlpha: " + query)

        preference_location = get_user_prefs(message)["location"]
        lat = str(preference_location['lat'])
        lng = str(preference_location['lng'])
        units = str(get_user_prefs(message)["units"]["measure"])
        query_type = "short" if message.context.get("klat_data") else "spoken"
        key = (utterance, lat, lng, units, repr(query_type))

        # TODO: This should be its own intent or skill DM
        if "convert" in query:
            to_convert = utt_query[:utt_query.index(utt_query.split(" ")[-1])]
            query = f'convert {to_convert} to {query.split("to")[1].split(" ")[-1]}'
        LOG.info(f"query={query}")

        kwargs = {"lat": lat, "lon": lng, "api": query_type, "units": units, "query": query}

        try:
            result = request_backend("proxy/wolframalpha", kwargs)
        except Exception as e:
            LOG.error(e)
            result = None
        LOG.info(f"result={result}")
        # TODO: get_wolfram_alpha_response should return status to check; these
        #   are all 501 return cases
        if result in ("Wolfram Alpha did not understand your input",
                      "Wolfram|Alpha did not understand your input",
                      "No spoken result available",
                      "No short answer available",
                      None):
            LOG.error("Got error result")
            return None, None

        return result, key
