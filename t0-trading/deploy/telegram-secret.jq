if type == "object"
  and (keys | sort == ["bot_token", "chat_id"])
  and (.bot_token | type == "string" and length > 0)
  and (.chat_id | type == "string" and length > 0)
then {bot_token, chat_id}
else error("Telegram secret must contain only populated bot_token and chat_id strings")
end
