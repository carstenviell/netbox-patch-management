from django.utils.translation import gettext_lazy as _

from netbox.plugins.navigation import PluginMenu, PluginMenuButton, PluginMenuItem

menu = PluginMenu(
    label=_('Patch Management'),
    icon_class='mdi mdi-update',
    groups=((_('Patch Management'), (
        PluginMenuItem(link='plugins:netbox_patch_management:planning', link_text=_('Planning'),
                       permissions=['netbox_patch_management.view_updateclass']),
        PluginMenuItem(
            link='plugins:netbox_patch_management:updateclass_list', link_text=_('Update classes'),
            permissions=['netbox_patch_management.view_updateclass'],
            buttons=(PluginMenuButton(link='plugins:netbox_patch_management:updateclass_add', title=_('Add'),
                                      icon_class='mdi mdi-plus-thick',
                                      permissions=['netbox_patch_management.add_updateclass']),),
        ),
        PluginMenuItem(link='plugins:netbox_patch_management:patchlog_list', link_text=_('Patch log'),
                       permissions=['netbox_patch_management.view_patchlog']),
    )),),
)
