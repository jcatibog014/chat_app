# Paper Plane

**Send it flying.** A chat app where messages arrive the moment they are sent.

You can talk to one person (a direct message) or to a group. You see who is online, who is typing, and how many messages you haven't read. Everything changes live, without reloading the page.

![The chat list and a group chat](docs/screenshots/group-chat.png)

## Try It

Open the app and click **Try the demo**. You are Mika, a designer at a small studio called Papel Studio. Your teammates (Ana, Ben, Carlo and Dana) are made up, but they come online, type and answer you. The demo is only for you, and it is deleted after 24 hours.

Want to see two real people chat? Click your picture (at the bottom of the menu, or **Profile** on a phone), then **Invite a friend**. Open the link on your phone or in a private window.

## What It Can Do

| Part | What you can do |
| --- | --- |
| Chats | Message one person, or make a group. Your chats are in a list, newest first. |
| Live | New messages, "is typing", and "Seen" arrive at once, over a WebSocket. |
| Unread | Each chat shows how many messages you haven't read. The menu shows the total. |
| Online | A green dot shows who is online. You also see "Away" and "Last seen 5 min ago". You can hide your own. |
| Message tools | Reply to a message, edit your own, delete for everyone, and react with an emoji. |
| Groups | Owners and admins can rename, add and remove people, make admins, mute someone, and make invite links. |
| Stories | Share a photo or a few words on a colour. A story lasts 24 hours. Show it to everyone you chat with, or only your Close Friends. See who saw it, get reactions and private replies, and mute someone's stories. |
| Blocking | Block someone: neither of you can message the other, or see the other online. |
| Pictures and files | Send pictures and files (up to 5 MB). Pictures lose their hidden details (like where a photo was taken). |
| Link previews | A web address in a message gets a small card with the page's title. |
| Photos, Files and Links | Every chat keeps a history of the pictures, files and links sent in it, newest first, one tab each. |
| Find | Search the words in all your chats, or in one chat (and choose who said them). |
| Mentions | Write @name in a group. That person gets a red @ and a notification. |
| Notifications | Notifications on your computer and phone. Mute a chat to make it quiet. |
| Colours | Pink (like the logo) is the default. You can choose purple, red or blue, and light or dark. |
| Settings | Settings has pages: Account, Appearance, Notifications, Privacy and Devices. |
| App | Install Paper Plane on your phone's home screen. |
| Demo | Try the demo, with made-up teammates who answer. |

## Pictures

### Logging In

| Log in | Create an account |
| --- | --- |
| ![The login page](docs/screenshots/login.png) | ![Create an account](docs/screenshots/signup.png) |

### Chatting Live

Your chats, newest first. Bold chats have unread messages. A grey number means the chat is muted. A red @ means someone mentioned you. The two buttons above the list open **Search** and **New Chat** on the right side of the same page.

![The chat list](docs/screenshots/chats.png)

You send a message. Ana reads it ("Seen") and starts typing. Then her answer arrives.

| Ana is typing | Ana answers |
| --- | --- |
| ![Seen and Ana is typing](docs/screenshots/live-typing.png) | ![Ana's answer](docs/screenshots/live-reply.png) |

### Message Tools

| The message menu | Replying |
| --- | --- |
| ![Reactions and Reply](docs/screenshots/message-menu.png) | ![Replying to a message](docs/screenshots/reply.png) |

| A link preview, and a muted chat | A file |
| --- | --- |
| ![A link card](docs/screenshots/link-and-mute.png) | ![A file in a chat](docs/screenshots/file.png) |

### Groups and People

| Group info (you own the group) | What you can do to a member |
| --- | --- |
| ![Group info](docs/screenshots/group-info.png) | ![The member menu](docs/screenshots/member-menu.png) |

| Chat info and Block | New chat |
| --- | --- |
| ![Chat info](docs/screenshots/chat-info.png) | ![New chat](docs/screenshots/new-chat.png) |

### Stories

A story is a photo or a few words on a colour, and it disappears after 24 hours. The circles above your chats show who has one: a coloured ring means something you haven't seen yet, and a grey ring means you have seen it all. The **Stories** page shows yours and everyone else's.

![The Stories page](docs/screenshots/stories.png)

Open a story and it plays for 5 seconds, then the next one starts. Tap the left side to go back, and the right side to skip. React with an emoji, or write a reply: the reply goes to your chat with that person, privately. Typing pauses the story.

| Watching a story | Who saw yours |
| --- | --- |
| ![Watching Ana's story](docs/screenshots/story-viewer.png) | ![Seen by, with Ben's reaction](docs/screenshots/story-seen-by.png) |

Each time you post, you choose who can see it: **everyone you chat with**, or only your **Close Friends** (Settings > Privacy). A reply to your story shows a small picture of it in the chat, or "Story expired" after 24 hours.

| A new story | A reply in the chat |
| --- | --- |
| ![Making a text card](docs/screenshots/story-new.png) | ![Carlo's reply to Mika's story](docs/screenshots/story-reply.png) |

Only people you chat with can see your stories, and someone you block can't see them at all. Only you can see who saw your story. If you don't want to see someone's stories, open one and choose **More**, then **Mute**. They aren't told.

### Search in a Chat

The magnifying glass at the top of a chat searches only that chat. Results show as you type, newest first. Choose a person to see only what they said (or, with no words, everything they said). Click a result: the chat opens at that message, with your words marked, and **Back to results** takes you back.

| Searching Papel Studio | A result, opened in the chat |
| --- | --- |
| ![Search in Papel Studio](docs/screenshots/chat-search.png) | ![The message, with the words marked](docs/screenshots/chat-search-found.png) |

### Photos, Files and Links

Every chat keeps the pictures, files and links sent in it. Open the chat's info (the **i** at the top of the chat): the latest photos are there, with how many photos, files and links the chat has. **See all** shows them, one tab each, newest first. **Show in chat** takes you to the message they came with.

| Photos | Links |
| --- | --- |
| ![The photos sent in Papel Studio](docs/screenshots/media-photos.png) | ![The links sent in Weekend Hike](docs/screenshots/media-links.png) |

You only see what you could read in the chat itself: if a group hides its earlier messages from new members, it hides their photos, files and links too. A deleted message takes its picture, file or link with it.

### Search, Your Profile and Settings

| Search | Settings |
| --- | --- |
| ![Search](docs/screenshots/search.png) | ![Settings](docs/screenshots/settings.png) |

Click your picture to open your profile menu: Settings, and Log out.

![The profile menu](docs/screenshots/profile-menu.png)

### Colours

Settings has a page for each part: Account, Appearance, Notifications, Privacy and Devices. In **Appearance** you choose the app's colour: pink (the logo's colour, the default), purple, red or blue. Your colour is saved to your account, so it is the same on every device.

![The Appearance page](docs/screenshots/appearance.png)

| Purple | Blue |
| --- | --- |
| ![The app in purple](docs/screenshots/colour-purple.png) | ![The app in blue](docs/screenshots/colour-blue.png) |

### Two Real People

Send a friend a link. They play Ben, and the made-up Ben steps aside.

| The links | Your friend opens one |
| --- | --- |
| ![Invite a friend](docs/screenshots/invite-friend.png) | ![Join as Ben](docs/screenshots/join-demo.png) |

![Your friend's message arrives live](docs/screenshots/friend-live.png)

### Dark Mode and Phones

![Dark mode](docs/screenshots/dark-chat.png)

On a phone there is no menu at the top. The bar at the bottom has **Chats**, **Stories** and **Profile**. Inside a chat the bar steps aside, and the arrow takes you back.

| Chats on a phone | Profile on a phone | A chat on a phone |
| --- | --- | --- |
| ![The chat list on a phone](docs/screenshots/phone-chats.png) | ![The profile menu on a phone](docs/screenshots/phone-profile.png) | ![A chat on a phone](docs/screenshots/phone-chat.png) |

| Stories on a phone | A story on a phone |
| --- | --- |
| ![The Stories page on a phone](docs/screenshots/phone-stories.png) | ![Carlo's Close Friends story on a phone](docs/screenshots/phone-story.png) |

## How It Works

**WebSockets.** A normal web page asks the server for something and gets an answer. A WebSocket stays open, so the server can send you news at any time: a new message, "Ana is typing", "Ben is online". Paper Plane opens one WebSocket for each open tab.

**Events.** Every message on the WebSocket is a small piece of JSON with a `type`, like `message.send` or `typing`. One router (`events.py`) checks each one in the same order: is it the right shape, are you still logged in, are you sending too fast, are you allowed? Then it does the work and answers "ack" (done) or "error".

**Safe to send twice.** Each message has its own id from your browser. If the connection breaks and your browser sends it again, the server saves it only once.

**Catching up.** If your connection breaks, the page tries again: after 1 second, then 2, 4, up to 30. When it is back, it asks "what did I miss?" and gets only the new things.

**Stories.** The rules for stories are in one file too (`stories.py`): you may see a story only if it is less than 24 hours old, you share a chat with its owner, nobody blocked anybody, and, for a Close Friends story, you are on the list. Anything else is "not found". The pictures are checked the same way, so a story's address is no use to anyone else. A new story, a view or a reaction goes out over the WebSocket only to the people who may see it.

**Who may do what.** All the rules are in one file (`access.py`). If you are not in a chat, the app says "not found", not "forbidden": a stranger can't even learn that the chat exists. When someone is removed from a group, they stop getting its messages at once. Only people who share a chat with you can see that you are online.

**One server.** Paper Plane remembers who is connected in the server's memory. So it runs as ONE server process (about 60 people at once on the free plan). To use more servers, they would need to pass messages to each other (for example with PostgreSQL `LISTEN/NOTIFY`). That part is explained here, not built.

## Tools

| Tool | What it does here |
| --- | --- |
| Python 3.13 | The language of the server. |
| Flask | The web framework: pages, forms and login. |
| flask-sock (simple-websocket) | The WebSocket endpoint, `/ws`. |
| JavaScript | The live parts in the browser (no framework). |
| SQLAlchemy and Alembic | The database code, and changes to the tables. |
| SQLite | The database on your computer and in the tests. |
| PostgreSQL (Neon) | The database on the internet. |
| Pillow | Re-saves pictures safely, and makes small copies. |
| CSS custom properties | The four colour palettes, each in light and dark. |
| pywebpush | Phone and computer notifications (web push). |
| pytest | The tests (300 of them). |
| Render | The web server on the internet (free plan). |

## Run It on Your Computer

```
cd C:\Users\USER\Documents\Workspace\chat_app
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\flask --app paperplane upgrade-db
.venv\Scripts\flask --app paperplane run --debug --port 5015
```

Then open http://127.0.0.1:5015.

Run the tests:

```
.venv\Scripts\python -m pytest
```

## Put It on the Internet

See [docs/DEPLOY.md](docs/DEPLOY.md): Neon for the database, Render for the server.
