## Many to Many Tables

### assistant_charactetrait

```python
assistant_charactertrait = db.Table(
    # table for the many to many relationship between assistant and charactertrait tables
    "assistant_charactertraits",
    db.Column(
        "assistant_id",
        db.Integer,
        db.ForeignKey("assistants.id", ondelete="CASCADE")
    ),
    db.Column(
        "charactertrait_id",
        db.Integer,
        db.ForeignKey("character_traits.id", ondelete="CASCADE")
    )
)
```
Many to many relationship between the Assistant and the CharacterTrait models. 

### collection_sources
```python
collection_sources = db.Table(
    # table for the many to many relationship between collection and source tables
    "collection_sources",
    db.Column(
        "collection_id", db.Integer, db.ForeignKey("collections.id", ondelete="CASCADE")
    ),
    db.Column("sources", db.Integer, db.ForeignKey("sources.id", ondelete="CASCADE")),
)
```
Many to many relationship between the Collection and the Sources models.

## DB Models

### Users

```python
class User(db.Model, UserMixin):
    # schema for the user table
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String, nullable=True)
    email = db.Column(db.String(66), nullable=False)
    f_name = db.Column(db.String(20), nullable=True)
    l_name = db.Column(db.String(20), nullable=True)
    password = db.Column(db.String(255), nullable=False)
    verified = db.Column(db.Boolean, default=False)
    
    def sign_up_with_google(self):
        """Method to sign up with google

        Args:
            self (User): the user object

        Returns:
            User: the user object
        """
        self.password = argon2.generate_password_hash(
            os.getenv("DEFAULT_GOOGLE_PASSWORD")
        )
        self.verified = True
        return self
    
    def sign_up_with_email(self, password=None):
        """Method to sign up using the email

        Args:
            self (User): the user object
            password (str): the user password

        Returns:
            User or Nonetype: the user object if there's both email and password or None
        """
        if password is not None:
            self.password = argon2.generate_password_hash(password)
            return self
        else:
            return None
    
    def check_password(self, password):
        """Method to check the user password

        Args:
            password (str): the password to check

        Returns:
            bool: password check
        """
        if type(password) == str:
            return argon2.check_password_hash(self.password, password)
        return False
    
    def change_password(self, old_password, new_password):
        """Method to change the password

        Args:
            old_password (str): the old password
            new_password (str): the new password
        
        Returns:
            bool: password changed
        """
        if type(old_password) == str and type(new_password) == str:
            if self.check_password(old_password):
                self.password = argon2.generate_password_hash(new_password)
                return True
        return False
```

I will let google users sign-in as "guest", disabling their ability to delete anything they saved until they complete the registration process adding a password.

### Assistant

```python
class Assistant(db.Model):
    # schema for the assistant table
    __tablename__ = "assistants"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(32), nullable=False)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )
    prompt = db.Column(db.String(255), nullable=False)
    traits = db.relationship(
        "CharacterTrait",
        secondary=assistant_charactertrait,
        backref="assistants",
        lazy=True
    )
    share = db.Column(db.Boolean, default=False)
    safe = db.Column(db.Boolean, default=False)
    avatar = db.Column(db.String, nullable=True)
    
    def retrieve_system_prompt(self) -> str:
        """Method to retrieve the system prompt

        Args:
            self (Assistant): the assistant object
        
        Returns:
            str: the system prompt
        """
        prompt = f"{self.prompt}\n"
        
        if len(self.traits) > 0:
            for trait in self.traits:
                prompt = prompt + f"""
Below there is a list of character traits with assigned
a number and the reason why. The number will be on a scale
between 1 and 10 where 1 is the minimum and 10 is the maximum.
You MUST answer accordingly to your character traits.
You MUST NOT share your character traits scores with the user.
You MUST NOT share your logic.

{trait.trait.capitalize()}: {trait.value}
{trait.reason_why}\n
"""
        prompt = prompt + "You MUST answer in the language used by the user."
        return prompt
```

The method retrieve_system_prompt should return the compiled assistant prompt.

### CharacterTrait

```python
class CharacterTrait(db.Model):
    # schema for the charactertrait table
    __tablename__ = "character_traits"
    id = db.Column(db.Integer, primary_key=True)
    trait = db.Column(db.String(32), nullable=False)
    value = db.Column(db.Integer, nullable=False)
    reason_why = db.Column(db.String(255), nullable=False)
```

### ChatHistory

```python
class ChatHistory(db.Model):
    # schema for the chat history table
    __tablename__ = "chat_histories"
    id = db.Column(db.Integer, primary_key=True)
    messages = db.Column(JSON)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE")
    )
```

### Collections
```python
class Collection(db.Model):
    # schema for the collection table
    __tablename__ = "collections"
    id = db.Column(db.Integer, primary_key=True)
    collection_name = db.Column(db.String(32), nullable=False)
    documents_description = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    sources = db.relationship(
        "Source", secondary=collection_sources, backref="collections", lazy=True
    )
    share = db.Column(db.Boolean, default=False)
    safe = db.Column(db.Boolean, default=False)
    vectorstore_id = db.Column(db.String, nullable=True)
```

### Source
```python
class Source(db.Model):
    # schema for the source table
    __tablename__ = "sources"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(16), nullable=True)
    description = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    aws_key = db.Column(db.String, nullable=False)

    def __repr__(self):
        return self.description
```

### BackgroundIngestionTask
```python
class BackgroundIngestionTask(db.Model):
    # schema for the background task table
    __tablename__ = "background_ingestion_tasks"
    id = db.Column(db.Integer, primary_key=True)
    collection_id = db.Column(
        db.Integer, db.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    source_id = db.Column(
        db.Integer, db.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    heroku_task_id = db.Column(db.String, nullable=True)
    proprietary_task_id = db.Column(db.String, nullable=True)
    result = db.Column(db.Boolean, nullable=True)
    ended = db.Column(db.Boolean, default=False)
```

### DailyTokens
```python
class DailyTokens(db.Model):
    # schema for the user daily token usage
    __tablename__ = "daily_tokens"
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.String(10), nullable=False)
    prompt_tokens = db.Column(db.Integer, nullable=False)
    completion_tokens = db.Column(db.Integer, nullable=False)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
```
