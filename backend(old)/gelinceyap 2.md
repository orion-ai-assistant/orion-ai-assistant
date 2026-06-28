1-🧠 Active Agent
Select the active agent profile. da hiçbir agent yok seçemiyorum
2- agentları görebildiğim bir panelde yok onu da ekler misin agentları göreyim onların toolarını düzenliyebileyim ekle azalt vs.
---
tamam güzel ama bir kaç değişiklik lazım.
1- tool , tools kısmından devre dışı olsa bile biz onu seçebiliyoruz agent profiles da öyle olmucak . yapı katmanlı olarak ilerlicek mesela biz eğer devre dışı bıraktıysak tools kısmında o zaman bunu agentlar devre dışı olarak görecek seçemeyecek yani anladın mı devre dışı dicek seçili olsa bile kullanamıcak. 
2- ayrıca agent tool seçmese bile eğer o tool aktifse genel tools kısmında yine kullanabiliyor bu da üste doğru akan bir yapı olmalı seçili değilse aktif olsa da kulllanamaz.
3-bu agentlar ayrı bir panelde olsunlar.
4- "Active Agent System Prompt" diye bir şey olmasın her yerden kaldır yani görmemize gerekyokki zaten agentlar için system promtlar varya.
5- "🧠 Active Agent" bunu da agent paneli oluşturcazya oraya koyalım bence.
6-think mode her agentın kendi içinde olsun genel bir think olmasın. sadece agentlarda olsun açarsak kullabilsin vs. bak burada yine çıktı bunun gibi olmalı yani think modunu kapatmıyoruz sadece agent kısmında her agentın kendine özel think budget kullanılmasını sağlıyoruz. "  📥 LLM'den GELEN (RESPONSE) AIMessage
{
  "content": [
    {
      "type": "thinking",
      "thinking": "**Crafting a Natural Reply**\n\nI'm working on crafting a human-sounding response to the greeting. It's a simple \"how are you,\" so I want to reply with something that feels natural, maybe adding a small detail about my current state to avoid a generic bot-like answer. Maintaining my persona is key.\n\n\n",
      "index": 0
    },
    {
      "type": "text",
      "text": "iyiyim yaa,",
      "extras": {
        "signature": "Ck8Bvj72+6supOt2nHDSwegq77WhxBdSvTGIIRpahn8rqkLWHIV5HR42QKJBcWzYgx3ZNWFSk6Q+AOr796AGqFw9whnVy8H8P7wq8rpuTDiqCvMBAb4+9vtwCjjlulRqOX4MazVbJyjYkpsNavKdrMAlfXL2aqgIfxN5v4us5CkNH+jqU/hmdfSeMBKfF1FlrpfNepevsGeZ4Ul+WnUq7xbcXWzt+y/DENJf2kCpOgN29AGa81ASYY1I10lPpLaTkB8uoxJPOPPSZduZn+kYIwb0ZgGwewl9y2+aw9wGf8uWFWq7NZYiJ4W0sPIarpm6G/nutEoxfcK4eYWcppb4jRUuhhXpQ9EvNAxIOuDjhIdCv+nqoIV5Oy6fzIXKPEgLmRwn6jnVuUsFNjnHIuQgtVc+/r40G3XLPnpEpAc1EMtzKULXAFQq"
      },
      "index": 1
    },
    " öyle takılıyorum. sen napıyosun bakalım?"
  ],
  "additional_kwargs": {},
  "response_metadata": {
    "safety_ratings": [],
    "model_provider": "google_genai",
    "finish_reason": "STOP",
    "model_name": "gemini-2.5-flash"
  },
  "type": "ai",
  "name": null,
  "id": "lc_run--019d0139-3d55-7263-9981-96a9213e047f",
  "tool_calls": [],
  "invalid_tool_calls": [],
  "usage_metadata": {
    "input_tokens": 959,
    "output_tokens": 73,
    "total_tokens": 1032,
    "input_token_details": {
      "cache_read": 0
    },
    "output_token_details": {
      "reasoning": 57
    }
  }
}
────────────────────────────────────────────────────────────"
7-
bu değişiklikleri yaparken alttaki duruma dikkat et.
sorun ( eğer hiçbir tool yoksa yani en başta yeni bir agent eklendiğinde bütün toollar ekliymiş gibi sanıyor ama hem admin panelde hemde ai config jsonda hiçbir tool yok olarak görünüyor sorun buymuş. ama az önce bunu düzeltmiş olabilirsin emin değilim, eğer düzeltmediysen düzelt yoksa görmezden gel. )az önce böyle bir sorun oldu da buna da dikkat et.