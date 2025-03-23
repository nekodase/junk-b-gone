Bullshit I clocked up with chatgpt in a couple of hours
took more hours to figure out the absolute shitshow that is google cloud platform than the actual code
why is gmail api under gcp anyways?
also 4o decided to fuck with me a little and decided to keep insisting `openai.ChatCompletion` was up to date cause it thought 0.28 was the latest openai api or something jfc

- `get_uncategorized_messages`
  Ignores Google prefab labels: Promotions, Social, Updates. Cause they don't show up in inbox anyways.
  
- `classify_email_with_chatgpt`
  We don't actually label the mails in one go; I instructed 4o mini to create a 10-token summary first. idk it tends to do stupid shit like labeling airline ticket under "appointments" if i dont do that
  
- `process_message`
  yeah we actually label shit here
  we also log here, can you believe me if i say the log actually helped me a lot debugging
  
- `main`
  main

gg
