from types import SimpleNamespace as NS
import unittest
from telethon.tl.custom.participantpermissions import ParticipantPermissions
from telethon.tl.types import ChannelParticipantAdmin,ChannelParticipantCreator,ChatAdminRights
from mtproto_client import MTProto,TelegramError

class Client:
    def __init__(self,permissions=None,dialogs=()):
        self.permissions=permissions;self.dialogs=dialogs;self.limit='unset';self.failure=None
    async def get_entity(self,channel):
        self.resolved=channel
        return NS(id=123456,broadcast=True,megagroup=False,creator=False,title='Private')
    async def get_me(self): return NS(username='owner')
    async def get_permissions(self,entity,me):
        if self.failure: raise self.failure
        return self.permissions
    async def iter_dialogs(self,limit=None):
        self.limit=limit
        for item in self.dialogs: yield item

class ChannelTests(unittest.TestCase):
    def wrapper(self,client): return MTProto(None,'12345','a'*32,client=client)
    def permissions(self,post):
        return ParticipantPermissions(ChannelParticipantAdmin(1,2,None,ChatAdminRights(post_messages=post)),False)

    def test_real_telethon_admin_can_publish_private_channel(self):
        permissions=self.permissions(True)
        self.assertFalse(hasattr(permissions,'admin_rights'))
        client=Client(permissions)
        self.assertEqual(self.wrapper(client).verify('-100123456')['channel_title'],'Private')
        self.assertEqual(client.resolved,-100123456)

    def test_real_admin_without_post_right_is_rejected(self):
        with self.assertRaisesRegex(TelegramError,'account_permission'):
            self.wrapper(Client(self.permissions(False))).verify('-100123456')

    def test_real_creator_permissions_are_accepted(self):
        permissions=ParticipantPermissions(ChannelParticipantCreator(1,ChatAdminRights()),False)
        self.assertEqual(self.wrapper(Client(permissions)).verify('-100123456')['account'],'@owner')

    def test_permission_lookup_network_failure_is_not_reported_as_denial(self):
        client=Client();client.failure=TimeoutError('private details')
        with self.assertRaisesRegex(TelegramError,'connection_timeout'): self.wrapper(client).verify('-100123456')

    def test_channel_list_includes_private_and_public_writable_channels_only(self):
        def dialog(i,title,username='',creator=False,post=False,broadcast=True):
            return NS(id=i,entity=NS(title=title,username=username,creator=creator,broadcast=broadcast,admin_rights=ChatAdminRights(post_messages=post)))
        private=dialog(-100123456,'Private',post=True)
        client=Client(dialogs=[private,dialog(-100234567,'Public','example_channel',True),
                               dialog(-100345678,'Read only'),dialog(-100456789,'Group',post=True,broadcast=False),private])
        result=self.wrapper(client).list_channels()
        self.assertEqual(result,[{'id':'-100123456','title':'Private','username':''},
                                 {'id':'-100234567','title':'Public','username':'example_channel'}])
        self.assertIsNone(client.limit)

    def test_empty_channel_list(self):
        self.assertEqual(self.wrapper(Client()).list_channels(),[])
