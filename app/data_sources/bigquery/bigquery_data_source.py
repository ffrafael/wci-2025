# Copyright 2022 Google LLC.
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

"""
A BigQuery extension for Data Sources
"""

import json
import os
import datetime
from typing import Dict, Optional
from google.cloud import bigquery
from data_sources import DataSource

BQ_PENDING_LEAD_TABLE = os.environ.get("BQ_PENDING_LEAD_TABLE")
BQ_LEAD_TABLE = os.environ.get("BQ_LEAD_TABLE")
BQ_CHAT_TABLE = os.environ.get("BQ_CHAT_TABLE")


class BigQueryDataSource(DataSource):
    """BigQuery as datasource"""

    def __init__(self):
        # TODO(mr-lopes): adds client settings such as location
        self._bq_client = bigquery.Client()

    def save_protocol(
        self,
        identifier: str,
        type: str,
        protocol: str,
        mapped: Optional[Dict[str, str]],
    ):
        """
        Saves a protocol number with identifier and mapped values

        Parameters:
            identifier: gclid, client_id, etc
            type: indicates the type of identifier (gclid, etc)
            protocol: a generated protocol
            mapped: any additional value[s] to be associated with the protocol
        """

        rows_to_insert = [
            {
                "identifier": identifier,
                "type": type,
                "protocol": protocol,
                "mapped": json.dumps(mapped) if mapped else None,
                "timestamp": datetime.datetime.now().timestamp(),
            }
        ]

        errors = self._bq_client.insert_rows_json(BQ_PENDING_LEAD_TABLE, rows_to_insert)

        if not errors == []:
            raise Exception("Error while creating pending-lead: {}".format(errors))

    def save_phone_protocol_match(self, phone: str, protocol: str, name: str):
        """
        Saves a protocol matched to a number (phone)

        Parameters:
            phone: phone number
            protocol: protocol sent by phone number
        """
        rows_to_insert = [
            {
                "phone": phone,
                "protocol": protocol,
                "timestamp": datetime.datetime.now().timestamp(),
                "name": name
            }
        ]

        errors = self._bq_client.insert_rows_json(BQ_LEAD_TABLE, rows_to_insert)

        if not errors == []:
            raise Exception("Error while creating lead: {}".format(errors))

    def save_message(self, message: str, sender: str, receiver: str):
        """
        Saves menssage sent by phone number (sender)

        Parameters:
            message: content of message
            sender: emitter
            receiver: recipient
        """

        rows_to_insert = [
            {
                "sender": sender,
                "receiver": receiver,
                "message": message,
                "timestamp": datetime.datetime.now().timestamp(),
            }
        ]

        errors = self._bq_client.insert_rows_json(BQ_CHAT_TABLE, rows_to_insert)

        if not errors == []:
            raise Exception("Error while creating chat-lead: {}".format(errors))

    def get_protocol_match(self, protocol: str, sender: str):
        """
        Gets the lead match for the protocol and sender

        Parameters:
            protocol: matched protocol
            sender: emitter

        """

        query = f"""
            SELECT 
                plead.identifier, plead.type, plead.protocol, plead.mapped
            FROM `{BQ_PENDING_LEAD_TABLE}` AS plead
            INNER JOIN  `{BQ_LEAD_TABLE}` AS lead USING (protocol)
            WHERE plead.protocol = @protocol
            AND lead.phone = @sender
            LIMIT 1
            """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("protocol", "STRING", protocol),
                bigquery.ScalarQueryParameter("sender", "STRING", sender),
            ]
        )

        rows = self._bq_client.query(query, job_config=job_config).result()

        # Maps the query's schema for later use
        query_schema = {sch.name: sch for sch in rows.schema}
        for row in rows:
            return self._convert_row_to_dict(row, query_schema)

    def _convert_row_to_dict(self, row, schema: dict = {}):
        """
        Converts a row into dict -- including json'd strings

        Parameters:
            row: a row from bq's query result
            schema: query's schema

        """
        dict = {}
        for key, value in row.items():
            # This is necessary because bq.client does not
            # automatically convert a stringify json into a dict
            if value and schema and schema[key].field_type.lower() == "json":
                # In case it's an array of  json, apply the proper
                # transformation
                if schema[key].mode.lower() == "repeated":
                    dict[key] = list(map(json.loads, value))
                else:
                    dict[key] = json.loads(value)
            else:
                dict[key] = value
        return dict
    
    def update_lead_email(self, phone: str, email: str):
        """
        Inserts a new lead version with email,
        reusing the protocol from the latest lead.
        Avoids UPDATE over streaming buffer.
        """
    
        # 1️⃣ Buscar lead mais recente pelo telefone
        select_query = f"""
            SELECT
                protocol, name
            FROM `{BQ_LEAD_TABLE}`
            WHERE phone = @phone
            QUALIFY
                ROW_NUMBER() OVER (
                    PARTITION BY phone
                    ORDER BY COALESCE(updated_at, timestamp) DESC
                ) = 1
        """
    
        select_job = self._bq_client.query(
            select_query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("phone", "STRING", phone),
                ]
            )
        )
    
        rows = list(select_job.result())
    
        # 2️⃣ Se não encontrou lead, não faz insert
        if not rows:
            return
    
        protocol = rows[0]["protocol"]
        name = rows[0]["name"]
    
        # 3️⃣ Inserir nova versão do lead com email
        insert_query = f"""
            INSERT INTO `{BQ_LEAD_TABLE}` (
                protocol,
                phone,
                email,
                timestamp,
                updated_at,
                name
            )
            VALUES (
                @protocol,
                @phone,
                @email,
                @timestamp,
                @updated_at,
                @name
            )
        """
    
        now = datetime.datetime.utcnow()
    
        insert_job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("protocol", "STRING", protocol),
                bigquery.ScalarQueryParameter("phone", "STRING", phone),
                bigquery.ScalarQueryParameter("email", "STRING", email),
                bigquery.ScalarQueryParameter("timestamp", "TIMESTAMP", now),
                bigquery.ScalarQueryParameter("updated_at", "TIMESTAMP", now),
                bigquery.ScalarQueryParameter("name", "STRING", name),
            ]
        )
    
        self._bq_client.query(insert_query, job_config=insert_job_config).result()

